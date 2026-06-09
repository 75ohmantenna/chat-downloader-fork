# SPDX-License-Identifier: MIT

"""Console output, encoding detection, and filename sanitization helpers."""

import io
import locale
import re
import sys
from typing import Any

# Windows console constants
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12
FILE_TYPE_CHAR = 0x0002
FILE_TYPE_REMOTE = 0x8000
MAX_BMP_CODEPOINT = 0xFFFF
WINDOWS_CONSOLE_BUFFER_SIZE = 1024

# Windows output handle mapping
WIN_OUTPUT_IDS = {
    1: STD_OUTPUT_HANDLE,
    2: STD_ERROR_HANDLE,
}


def preferredencoding() -> str:
    """Return the preferred locale encoding, falling back to UTF-8.

    Returns the best encoding scheme for the system, based on
    ``locale.getpreferredencoding()`` and some further tweaks.
    """
    try:
        pref = locale.getpreferredencoding()
        "TEST".encode(pref)
    except (LookupError, UnicodeError):
        pref = "utf-8"

    return pref


def _get_windows_console_handle(out: Any) -> Any:
    """Get Windows console handle for output stream.

    :param out: Output stream
    :return: Console handle or None if not available
    """
    import ctypes
    import ctypes.wintypes

    try:
        fileno = out.fileno()
    except (AttributeError, io.UnsupportedOperation):
        return None

    if fileno not in WIN_OUTPUT_IDS:
        return None

    # ctypes.WINFUNCTYPE and ctypes.windll are Windows-only;
    # absent from non-Windows ctypes stubs.
    GetStdHandle = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD
    )(
        ("GetStdHandle", ctypes.windll.kernel32),  # type: ignore[attr-defined]
    )

    return GetStdHandle(WIN_OUTPUT_IDS[fileno])


def _is_valid_console(handle: Any) -> bool:
    """Check if handle is a valid console.

    :param handle: Windows console handle
    :return: True if valid console, False otherwise
    """
    import ctypes
    import ctypes.wintypes

    INVALID_HANDLE_VALUE = ctypes.wintypes.DWORD(-1).value

    if handle == INVALID_HANDLE_VALUE or handle is None:
        return False

    # ctypes.WINFUNCTYPE and ctypes.windll are Windows-only;
    # absent from non-Windows ctypes stubs.
    GetFileType = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD
    )(
        ("GetFileType", ctypes.windll.kernel32),  # type: ignore[attr-defined]
    )

    GetConsoleMode = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )(("GetConsoleMode", ctypes.windll.kernel32))  # type: ignore[attr-defined]

    file_type = GetFileType(handle) & ~FILE_TYPE_REMOTE
    if file_type != FILE_TYPE_CHAR:
        return False

    return bool(GetConsoleMode(handle, ctypes.byref(ctypes.wintypes.DWORD())))


def _find_next_nonbmp_position(s: str) -> int:
    """Find position of next non-BMP character in string.

    :param s: String to search
    :return: Position of next non-BMP character, or length of string
    """
    try:
        return next(i for i, c in enumerate(s) if ord(c) > MAX_BMP_CODEPOINT)
    except StopIteration:
        return len(s)


def _write_to_windows_console(
    handle: Any, text: str, skip_errors: bool = True
) -> bool:
    """Write text to Windows console using WriteConsoleW API.

    :param handle: Windows console handle
    :param text: Text to write
    :param skip_errors: Whether to skip errors on write failure
    :return: True if successful
    :raises OSError: if write fails and skip_errors is False
    """
    import ctypes
    import ctypes.wintypes

    # ctypes.WINFUNCTYPE and ctypes.windll are Windows-only;
    # absent from non-Windows ctypes stubs.
    WriteConsoleW = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.LPWSTR,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.wintypes.LPVOID,
    )(("WriteConsoleW", ctypes.windll.kernel32))  # type: ignore[attr-defined]

    written = ctypes.wintypes.DWORD(0)

    while text:
        # Determine how many characters to write
        next_nonbmp = _find_next_nonbmp_position(text)
        count = min(next_nonbmp, WINDOWS_CONSOLE_BUFFER_SIZE)

        # Write to console
        ret = WriteConsoleW(
            handle,
            text,
            count or 2,
            ctypes.byref(written),
            None,
        )

        if ret == 0:
            if skip_errors:
                continue
            msg = "Failed to write string"
            raise OSError(msg)

        # Update position in string
        if not count:  # We just wrote a non-BMP character
            if written.value != 2:
                raise RuntimeError(
                    "Expected 2 code units for non-BMP character, "
                    f"got {written.value}"
                )
            text = text[1:]
        else:
            if written.value <= 0:
                raise RuntimeError(
                    "WriteConsoleW reported zero characters written"
                )
            text = text[written.value :]

    return True


def _windows_write_string(s: str, out: Any, skip_errors: bool = True) -> bool:
    """Write a string to a Windows console using special API methods.

    Returns True if the string was written using the Windows console API, False
    if it has yet to be written (i.e. the normal path should be used).
    """
    # Adapted from http://stackoverflow.com/a/3259271/35070

    handle = _get_windows_console_handle(out)
    if handle is None:
        return False

    if not _is_valid_console(handle):
        return False

    return _write_to_windows_console(handle, s, skip_errors)


def safe_print(
    *objects: object,
    sep: str = " ",
    end: str = "\n",
    out: Any = None,
    encoding: str | None = None,
    flush: bool = False,
) -> None:
    """Print objects to stdout safely across platforms.

    Handles Windows encoding issues that arise with emoji and non-UTF-8
    characters by routing output through the Windows console API when needed.
    """
    output_string = sep.join(str(x) for x in objects) + end

    if out is None:
        out = sys.stdout

    if sys.platform == "win32" and encoding is None and hasattr(out, "fileno"):
        if _windows_write_string(output_string, out):
            return

    if "b" in getattr(out, "mode", "") or not hasattr(out, "buffer"):
        out.write(output_string)
    else:
        enc = encoding or getattr(out, "encoding", None) or preferredencoding()
        byt = output_string.encode(enc, "ignore")
        out.buffer.write(byt)

    if flush and hasattr(out, "flush"):
        out.flush()


def pause(text: str = "Press Enter to continue...") -> None:
    """Block until the user presses Enter, showing a prompt.

    Args:
        text: Prompt text displayed to the user.
    """
    input(text)


# Reserved Windows device names (case-insensitive, optional extension).
# These names cannot be used as filenames on Windows regardless of directory.
_RESERVED_WINDOWS_NAMES_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$",
    re.IGNORECASE,
)

# Maximum filename byte length in UTF-8.  Most modern filesystems allow 255
# bytes; 200 gives comfortable headroom for extensions appended by callers.
_MAX_FILENAME_BYTES = 200


def sanitize_filename_component(
    text: str | None,
    replace_char: str = "_",
    max_length: int = _MAX_FILENAME_BYTES,
) -> str:
    r"""Sanitize a string for use as a single filename component (not a path).

    Intended for building filenames from user-supplied content such as stream
    titles or video IDs.

    WARNING: This function is NOT a path-security tool.  Only pass a single
    filename segment — never a full path — and always join the result with a
    trusted base directory via ``os.path.join()``.  The function does not
    strip ``..`` sequences because there is no base path to anchor against;
    that is the caller's responsibility.

    Replaces or removes:
    - Windows-forbidden characters: ``/ \\ : * ? " < > |``
    - ASCII control characters (0x00-0x1f, 0x7f)

    Strips:
    - Leading and trailing dots and spaces (cause issues on Windows/macOS)

    Avoids:
    - Reserved Windows device names (CON, PRN, AUX, NUL, COM0-COM9, LPT0-LPT9)
      — prefixes result with ``replace_char`` when matched

    Truncates to ``max_length`` UTF-8 bytes to prevent ENAMETOOLONG errors.
    A ``max_length`` of 0 disables truncation.

    :param text: Input string, or None (returns empty string).
    :param replace_char: Replacement character for forbidden chars, defaults
        to ``"_"``.
    :param max_length: Maximum byte length of the result in UTF-8, defaults
        to 200.  Pass 0 to disable.
    :return: Sanitized filename component string.
    """
    if text is None:
        return ""

    # 1. Replace Windows-hostile characters and path separators
    result = re.sub(r'[\\/:*?"<>|]', replace_char, text)

    # 2. Replace ASCII control characters (0x00-0x1f, 0x7f)
    result = re.sub(r"[\x00-\x1f\x7f]", replace_char, result)

    # 3. Strip leading/trailing dots and spaces
    result = result.strip(". ")
    if not result:
        return replace_char

    # 4. Prefix reserved Windows device names to make them safe
    if _RESERVED_WINDOWS_NAMES_RE.match(result):
        result = replace_char + result

    # 5. Truncate to max_length UTF-8 bytes
    if max_length > 0:
        encoded = result.encode("utf-8")
        if len(encoded) > max_length:
            result = encoded[:max_length].decode("utf-8", errors="ignore")

    return result

# SPDX-License-Identifier: MIT

"""Console output and encoding detection helpers."""

from __future__ import annotations

import io
import locale
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
    GetStdHandle = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]  # noqa: N806 — Windows API name
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

    INVALID_HANDLE_VALUE = ctypes.wintypes.DWORD(-1).value  # noqa: N806 — Windows API constant name

    if handle == INVALID_HANDLE_VALUE or handle is None:
        return False

    # ctypes.WINFUNCTYPE and ctypes.windll are Windows-only;
    # absent from non-Windows ctypes stubs.
    GetFileType = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]  # noqa: N806 — Windows API name
        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD
    )(
        ("GetFileType", ctypes.windll.kernel32),  # type: ignore[attr-defined]
    )

    GetConsoleMode = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]  # noqa: N806 — Windows API name
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


def _write_to_windows_console(handle: Any, text: str, skip_errors: bool = True) -> bool:
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
    WriteConsoleW = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]  # noqa: N806 — Windows API name
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
                msg_0 = (
                    f"Expected 2 code units for non-BMP character, got {written.value}"
                )
                raise RuntimeError(msg_0)
            text = text[1:]
        else:
            if written.value <= 0:
                msg_0 = "WriteConsoleW reported zero characters written"
                raise RuntimeError(msg_0)
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

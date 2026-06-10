# SPDX-License-Identifier: MIT

"""Filename-component sanitization helpers."""

from __future__ import annotations

import re

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

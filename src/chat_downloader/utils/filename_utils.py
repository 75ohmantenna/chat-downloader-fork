# SPDX-License-Identifier: MIT

"""Filename-component sanitization helpers."""

from __future__ import annotations

import re
from string import Formatter

from chat_downloader.errors import InvalidParameter

# Reserved Windows device names (case-insensitive, optional extension).
# These names cannot be used as filenames on Windows regardless of directory.
_RESERVED_WINDOWS_NAMES_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$",
    re.IGNORECASE,
)

# Maximum filename byte length in UTF-8.  Most modern filesystems allow 255
# bytes; 200 gives comfortable headroom for extensions appended by callers.
_MAX_FILENAME_BYTES = 200


def validate_output_template(file_name: str) -> None:
    """Accept only simple title and id placeholders in an output path."""
    try:
        fields = Formatter().parse(file_name)
        for _literal, field, format_spec, conversion in fields:
            if field is not None and (
                field not in {"title", "id"} or format_spec or conversion
            ):
                msg = "Output paths support only {title} and {id} placeholders."
                raise InvalidParameter(msg)
    except ValueError as error:
        msg = f"Invalid output path placeholder: {error}"
        raise InvalidParameter(msg) from error


def sanitize_filename_component(
    text: str | None,
    replace_char: str = "_",
    max_length: int = _MAX_FILENAME_BYTES,
) -> str:
    r"""Sanitize a string as one filename component (never a path).

    Not a path-security tool: pass a single segment, never a full path;
    ``..`` is not stripped (no base path to anchor against). Replaces
    Windows-forbidden characters and ASCII control chars with
    ``replace_char``, strips leading/trailing dots and spaces, prefixes
    reserved Windows device names, and truncates to ``max_length`` UTF-8
    bytes (0 disables truncation). ``None`` returns an empty string.
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

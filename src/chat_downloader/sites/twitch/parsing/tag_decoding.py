# SPDX-License-Identifier: MIT

"""IRC tag decoding and boolean parsing helpers."""

from __future__ import annotations


def _parse_bool(text: str) -> bool:
    """Parse IRC boolean (1 = true, 0 = false)."""
    return text == "1"


def _parse_bool_text(text: str) -> bool:
    """Parse text boolean ('true' = true, 'false' = false)."""
    return text == "true"


def _decode_pseudo_bnf(text: str) -> str:
    """Decode text according to IRC v3 message-tags spec."""
    return (
        text.replace(r"\\", "\x00BACKSLASH\x00")
        .replace(r"\:", ";")
        .replace(r"\s", " ")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
        .replace("\x00BACKSLASH\x00", "\\")
    )

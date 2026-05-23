# SPDX-License-Identifier: MIT

"""Helpers for scanning existing JSON arrays in append mode."""

from typing import IO


def find_last_non_whitespace(file: IO[str]) -> tuple[int | None, str]:
    """Return the position and value of the last non-whitespace character."""
    pos = file.seek(0, 2)
    while pos > 0:
        pos -= 1
        file.seek(pos)
        character = file.read(1)
        if character and character.isspace():
            continue
        return pos, character
    return None, ""


def find_previous_non_whitespace(file: IO[str], start: int) -> tuple[int, str]:
    """Return the nearest non-whitespace character at or before ``start``."""
    pos = start
    while pos >= 0:
        file.seek(pos)
        character = file.read(1)
        if character and character.isspace():
            pos -= 1
            continue
        return pos, character
    return 0, ""

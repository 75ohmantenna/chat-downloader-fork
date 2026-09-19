# SPDX-License-Identifier: MIT

"""ARGB/RGBA colour conversion helpers for chat badge and message colours."""

# Color conversion constants
from __future__ import annotations

RED_SHIFT = 16
GREEN_SHIFT = 8
BLUE_SHIFT = 0
ALPHA_SHIFT = 24
COLOR_MASK = 255


def argb_int_to_rgba(argb_int: int) -> list[int]:
    """Convert an ARGB integer to an RGBA component list.

    Args:
        argb_int: Packed ARGB integer.
    """
    red = (argb_int >> RED_SHIFT) & COLOR_MASK
    green = (argb_int >> GREEN_SHIFT) & COLOR_MASK
    blue = (argb_int >> BLUE_SHIFT) & COLOR_MASK
    alpha = (argb_int >> ALPHA_SHIFT) & COLOR_MASK
    return [red, green, blue, alpha]


def rgba_to_hex(colours: list[int]) -> str:
    """Convert RGBA components to a hexadecimal colour string.

    Args:
        colours: Red, green, blue, and alpha components in that order.
    """
    return f"#{colours[0]:02x}{colours[1]:02x}{colours[2]:02x}{colours[3]:02x}"

# SPDX-License-Identifier: MIT

"""Debugging module for chat_downloader."""

from __future__ import annotations

import logging
import os
import sys
from enum import Enum
from typing import Any

from .metadata import __name__ as logger_name
from .utils.console_utils import pause


class TestingException(Exception):
    """Raised when something unexpected happens while in testing mode."""


class TestingModes(Enum):
    """Testing modes controlling pause/exit behaviour on debug events."""

    EXIT_ON_ERROR = 4
    PAUSE_ON_ERROR = 3
    EXIT_ON_DEBUG = 2
    PAUSE_ON_DEBUG = 1
    NONE = 0


TESTING_MODE = TestingModes.NONE


def set_testing_mode(new_mode: TestingModes) -> None:
    """Set the global testing mode used by :func:`log` and :func:`debug_log`.

    Args:
        new_mode: The desired testing mode from :class:`TestingModes`.
    """
    global TESTING_MODE  # noqa: PLW0603 — module-level singleton mutated by test setup
    TESTING_MODE = new_mode


def log(level: str, items: Any, to_pause: bool = False, to_exit: bool = False) -> None:
    """Log one or more items at the given level, optionally pausing or raising.

    Args:
        level: Logger method name (e.g. ``"debug"``, ``"warning"``).
        items: A single item or list/tuple of items to log.
        to_pause: If True and the testing mode is PAUSE_ON_*, call
            :func:`~chat_downloader.utils.console_utils.pause`.
        to_exit: If True and the testing mode is EXIT_ON_*, raise
            :class:`TestingException`.
    """
    logger_at_level = getattr(logger, level, None)
    if logger_at_level:
        if not isinstance(items, (tuple, list)):
            items = [items]
        for item in items:
            logger_at_level(item)

        if to_exit and TESTING_MODE in (
            TestingModes.EXIT_ON_ERROR,
            TestingModes.EXIT_ON_DEBUG,
        ):
            msg = "Testing exception encountered, exiting program"
            raise TestingException(msg)

        if to_pause and TESTING_MODE in (
            TestingModes.PAUSE_ON_ERROR,
            TestingModes.PAUSE_ON_DEBUG,
        ):
            pause()


def debug_log(*items: Any) -> None:
    """Method which simplifies the logging of debugging messages."""
    log("debug", items, True, True)


try:
    import colorama

    colorama.init()
except (ImportError, OSError):
    HAS_COLORAMA = False
else:
    HAS_COLORAMA = True


def supports_colour() -> bool:
    """Return True if the running system's terminal supports colour.

    Returns False otherwise.

    Adapted from:
    https://github.com/django/django/blob/master/django/core/management/color.py
    """

    def vt_codes_enabled_in_windows_registry() -> bool:
        """Check the Windows Registry for enabled VT code handling.

        See https://superuser.com/a/1300251/447564 for background.
        """
        try:
            # winreg is only available on Windows.
            import winreg
        except ImportError:
            return False
        else:
            # winreg members are absent from non-Windows type stubs.
            reg_key = winreg.OpenKey(  # type: ignore[attr-defined]
                winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                "Console",
            )
            try:
                reg_key_value, _ = winreg.QueryValueEx(  # type: ignore[attr-defined]
                    reg_key, "VirtualTerminalLevel"
                )
            except FileNotFoundError:
                return False
            else:
                return bool(reg_key_value == 1)

    # isatty is not always implemented, #6223.
    is_a_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    return is_a_tty and (
        sys.platform != "win32"
        or HAS_COLORAMA
        or "ANSICON" in os.environ
        or
        # Windows Terminal supports VT codes.
        "WT_SESSION" in os.environ
        or
        # Microsoft Visual Studio Code's built-in terminal supports colors.
        os.environ.get("TERM_PROGRAM") == "vscode"
        or vt_codes_enabled_in_windows_registry()
    )


if supports_colour():
    import colorlog

    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "[%(log_color)s%(levelname)s%(reset)s] %(message)s",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        ),
    )

else:  # fallback support
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

# Create logger object for this module
logger = logging.getLogger(logger_name)

# Define which loggers to display
loggers = [logging.getLogger(name) for name in (logger_name, "urllib3")]
for configured_logger in loggers:
    configured_logger.addHandler(handler)


def set_log_level(level: str) -> None:
    """Set the log level for all chat-downloader loggers.

    Args:
        level: Level name such as ``"debug"``, ``"info"``, or ``"warning"``
            (case-insensitive).
    """
    level_name = level.upper()
    for logger in loggers:
        logger.setLevel(level_name)


def disable_logger() -> None:
    """Disable all chat-downloader loggers, suppressing all output."""
    for configured_logger in loggers:
        configured_logger.disabled = True


# Export public API
__all__ = [
    "TestingException",
    "TestingModes",
    "debug_log",
    "disable_logger",
    "log",
    "logger",
    "set_log_level",
    "set_testing_mode",
]

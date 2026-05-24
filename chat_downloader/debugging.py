# SPDX-License-Identifier: MIT

"""Debugging module for chat_downloader."""

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from .debug_sample_utils import (
    describe_debug_sample,
    slugify_debug_label,
)
from .metadata import __name__ as logger_name
from .utils.console_utils import pause


class TestingException(Exception):
    """Raised when something unexpected happens while in testing mode."""


class TestingModes(Enum):
    """Enumeration of testing modes controlling pause/exit behaviour on debug
    events.
    """

    EXIT_ON_ERROR = 4
    PAUSE_ON_ERROR = 3
    EXIT_ON_DEBUG = 2
    PAUSE_ON_DEBUG = 1
    NONE = 0


TESTING_MODE = TestingModes.NONE
REDACTED = "<redacted>"
_DEBUG_SAMPLE_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES"
_DEBUG_SAMPLE_DIR_ENV = "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_SENSITIVE_LOG_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "proxy",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    },
)

# Defense-in-depth: even after key-based redaction, raw token-like strings
# embedded in serialized payload values (e.g. an Authorization header
# concatenated into a log message, or a SAPISIDHASH copied into a string
# body) should be scrubbed before the sample lands on disk.
_TOKEN_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Tokens carried as JSON string values for any of the named keys.
    re.compile(
        r'(?i)("(?:authorization|sapisid(?:hash)?|set-cookie|cookie|'
        r'bearer|token|x-api-key|x-goog-authuser)"\s*:\s*")[^"]+',
    ),
    # Bare "Authorization: Bearer …" / "SAPISIDHASH …" style strings.
    re.compile(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-+/=]{8,}",
    ),
    re.compile(r"(?i)(sapisidhash\s+)[A-Za-z0-9_]{16,}"),
    # JWT-shaped payloads: header.payload.signature.
    re.compile(
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    ),
)


def _scrub_token_like_strings(serialized: str) -> str:
    """Redact token-like substrings from a JSON-serialized payload."""
    for pattern in _TOKEN_REDACTION_PATTERNS:
        if pattern.groups:
            serialized = pattern.sub(rf"\1{REDACTED}", serialized)
        else:
            serialized = pattern.sub(REDACTED, serialized)
    return serialized


def set_testing_mode(new_mode: TestingModes) -> None:
    """Set the global testing mode used by :func:`log` and :func:`debug_log`.

    Args:
        new_mode: The desired testing mode from :class:`TestingModes`.
    """
    global TESTING_MODE
    TESTING_MODE = new_mode


def sanitize_for_log(value: Any) -> Any:
    """Return a log-safe copy of ``value`` with sensitive fields redacted."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower() if isinstance(key, str) else key
            if normalized_key == "headers" and isinstance(item, dict):
                sanitized[key] = {
                    k: (
                        REDACTED
                        if isinstance(k, str)
                        and k.lower() in _SENSITIVE_LOG_KEYS
                        else v
                    )
                    for k, v in item.items()
                }
            elif (
                isinstance(normalized_key, str)
                and normalized_key in _SENSITIVE_LOG_KEYS
            ):
                sanitized[key] = REDACTED if item is not None else None
            else:
                sanitized[key] = sanitize_for_log(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_for_log(item) for item in value)

    return value


def log(
    level: str, items: Any, to_pause: bool = False, to_exit: bool = False
) -> None:
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


def _debug_sample_capture_enabled() -> bool:
    """Return ``True`` when debug sample capture is explicitly enabled."""
    env_value = os.environ.get(_DEBUG_SAMPLE_CAPTURE_ENV, "")
    return (
        logger.isEnabledFor(logging.DEBUG)
        and env_value.lower() in _TRUTHY_ENV_VALUES
    )


def capture_debug_sample(label: str, payload: Any) -> str | None:
    """Write a sanitized debug payload to a deterministic JSON file.

    This is opt-in and only active when:

    - the logger is in debug mode
    - ``CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES`` is set to a truthy value
    """
    if not _debug_sample_capture_enabled():
        return None

    try:
        sanitized = sanitize_for_log(payload)
        serialized = json.dumps(
            sanitized,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            default=str,
        )
        # Belt-and-braces scrub: tokens embedded in serialized string values
        # bypass the key-based redaction above.
        serialized = _scrub_token_like_strings(serialized)
        # Stable short digest for fixture names; not used for security
        # decisions.
        digest = hashlib.sha1(
            serialized.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:12]
        default_sample_dir = str(
            Path(tempfile.gettempdir()) / "chat_downloader_debug_samples"
        )
        sample_dir = Path(
            os.environ.get(_DEBUG_SAMPLE_DIR_ENV, default_sample_dir),
        )
        sample_dir.mkdir(parents=True, exist_ok=True)
        path = sample_dir / f"{slugify_debug_label(label)}-{digest}.json"
        if not path.exists():
            path.write_text(serialized + "\n", encoding="utf-8")
        hint = describe_debug_sample(path)
        logger.debug(
            "Captured debug sample: "
            f"path={path} "
            f"suggested_fixture_site={hint.site} "
            f"suggested_fixture_group={hint.group} "
            f"suggested_fixture_name={hint.fixture_name}",
        )
        return str(path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(f"Unable to capture debug sample for {label!r}: {exc}")
        return None


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
        """Check the Windows Registry to see if VT code handling has been
        enabled.

        See https://superuser.com/a/1300251/447564 for background.
        """
        try:
            # winreg is only available on Windows.
            import winreg
        except ImportError:
            return False
        else:
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
                return reg_key_value == 1

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
    "REDACTED",
    "TestingException",
    "TestingModes",
    "capture_debug_sample",
    "debug_log",
    "disable_logger",
    "log",
    "logger",
    "sanitize_for_log",
    "set_log_level",
    "set_testing_mode",
]

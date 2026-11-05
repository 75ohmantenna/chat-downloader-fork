# SPDX-License-Identifier: MIT

"""Retry policy dataclass with backoff, sleep, and user-prompt support."""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from threading import TIMEOUT_MAX
from typing import TYPE_CHECKING

from chat_downloader.errors import ChatDownloaderError

from .conversion_utils import backoff_seconds
from .timed_generator import (
    _POLLING_STOP_EVENT,
    _check_polling_cancelled,
    polling_sleep,
)
from .timed_input import timed_input

if TYPE_CHECKING:
    from collections.abc import Callable


_MAX_SLEEP_SECONDS = min(86_400, int(TIMEOUT_MAX))


def _finite_delay(value: object) -> float | None:
    """Accept only finite, nonnegative, representable numeric delays."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    if isinstance(value, int) and seconds < value:
        seconds = math.nextafter(seconds, math.inf)
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def parse_retry_after(headers: object, *, now: float | None = None) -> float | None:
    """Parse Retry-After delta-seconds or an HTTP date without capping its delay."""
    if not isinstance(headers, Mapping):
        return None
    value = next(
        (
            value
            for name, value in headers.items()
            if isinstance(name, str) and name.casefold() == "retry-after"
        ),
        None,
    )
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        try:
            return _finite_delay(int(value.lstrip("0") or "0"))
        except ValueError:
            return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        seconds = date.timestamp() - (time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def get_retry_after(error: object) -> float | None:
    """Recover a sanitized exception delay or its attached HTTP response header."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        delay = _finite_delay(getattr(error, "retry_after", None))
        if delay is not None:
            return delay
        response = getattr(error, "response", None)
        delay = parse_retry_after(getattr(response, "headers", None))
        if delay is not None:
            return delay
        error = getattr(error, "__cause__", None)
    return None


def _sleep_delay(seconds: float, sleep: Callable[[float], None]) -> None:
    """Split platform-overflowing waits without shortening the server minimum."""
    whole_seconds = int(seconds)
    fraction = seconds - whole_seconds
    while whole_seconds > _MAX_SLEEP_SECONDS:
        _check_polling_cancelled()
        sleep(_MAX_SLEEP_SECONDS)
        _check_polling_cancelled()
        whole_seconds -= _MAX_SLEEP_SECONDS
    _check_polling_cancelled()
    sleep(whole_seconds + fraction)
    _check_polling_cancelled()


def _stdin_is_interactive() -> bool:
    """Treat detached or closed input as noninteractive."""
    try:
        return sys.stdin.isatty()
    except (OSError, ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class RetryPolicy:
    """Unified retry policy for backoff and retry decision logic."""

    max_attempts: int = 1
    retry_timeout: object | None = None
    interruptible_retry: bool = True

    def can_retry(self, attempt_number: int) -> bool:
        """Return True when another attempt is allowed."""
        return attempt_number < self.max_attempts

    def sleep_seconds(self, attempt_number: int) -> float | None:
        """Return sleep seconds for this attempt, or None for manual pause."""
        timeout = self.retry_timeout
        if isinstance(timeout, (int, float)) and timeout < 0:
            return None
        if timeout is None or isinstance(timeout, (int, float)):
            return backoff_seconds(attempt_number, timeout)
        return None

    def sleep_text(
        self, attempt_number: int, *, interruptible: bool | None = None
    ) -> str:
        """Return human-readable sleep text used in retry logs."""
        seconds = self.sleep_seconds(attempt_number)
        if seconds is None:
            if isinstance(self.retry_timeout, (int, float)) and self.retry_timeout < 0:
                return "(press Enter to continue)"
            return ""
        use_interruptible = (
            self.interruptible_retry if interruptible is None else interruptible
        )
        if use_interruptible:
            return f"(sleep for {seconds}s or press Enter)"
        return f"(sleep for {seconds}s)"

    def wait(
        self,
        attempt_number: int,
        *,
        interruptible: bool | None = None,
        sleep_func: Callable[[float], None] | None = None,
        server_minimum: float | None = None,
    ) -> None:
        """Wait without allowing Enter to bypass a valid server minimum."""
        _check_polling_cancelled()
        seconds = self.sleep_seconds(attempt_number)
        minimum = _finite_delay(server_minimum) or 0.0
        sleep = sleep_func or (
            time.sleep if _POLLING_STOP_EVENT.get() is None else polling_sleep
        )
        if minimum > 0:
            _sleep_delay(max(seconds or 0.0, minimum), sleep)
            if seconds is not None:
                return
        if seconds is None:
            if not _stdin_is_interactive():
                msg = "Manual retry requires an interactive terminal."
                raise ChatDownloaderError(msg)
            try:
                timed_input(prompt="Press Enter to continue...")
            except EOFError as error:
                msg = "Manual retry input closed before Enter was pressed."
                raise ChatDownloaderError(msg) from error
            _check_polling_cancelled()
            return

        use_interruptible = (
            self.interruptible_retry if interruptible is None else interruptible
        )
        if use_interruptible and _stdin_is_interactive():
            timed_input(seconds)
            _check_polling_cancelled()
            return

        _sleep_delay(seconds, sleep)

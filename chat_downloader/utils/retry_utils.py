# SPDX-License-Identifier: MIT

"""Retry policy dataclass with backoff, sleep, and user-prompt support."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .console_utils import pause
from .conversion_utils import backoff_seconds
from .timed_utils import timed_input


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
        """Return sleep duration for this attempt, or None to request manual
        pause.
        """
        timeout = self.retry_timeout
        if isinstance(timeout, (int, float)) and timeout < 0:
            return None
        if timeout is None or isinstance(timeout, (int, float)):
            return backoff_seconds(attempt_number, timeout)
        return None

    def sleep_text(
        self, attempt_number: int, interruptible: bool | None = None
    ) -> str:
        """Return human-readable sleep text used in retry logs."""
        seconds = self.sleep_seconds(attempt_number)
        if seconds is None:
            if (
                isinstance(self.retry_timeout, (int, float))
                and self.retry_timeout < 0
            ):
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
    ) -> None:
        """Wait according to the policy before next retry."""
        seconds = self.sleep_seconds(attempt_number)
        if seconds is None:
            pause()
            return

        use_interruptible = (
            self.interruptible_retry if interruptible is None else interruptible
        )
        if use_interruptible:
            timed_input(seconds)
            return

        sleep = sleep_func or time.sleep
        sleep(seconds)

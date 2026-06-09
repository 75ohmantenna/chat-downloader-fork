# SPDX-License-Identifier: MIT

"""Type conversion utilities."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from collections.abc import Callable

_SYSTEM_RANDOM = random.SystemRandom()


def _convert_or_none[R, D](
    value: Any, converter: Callable[[Any], R], default: D
) -> R | D:
    """Generic conversion function with error handling.

    :param value: Value to convert
    :param converter: Conversion function (int, float, str, etc.)
    :param default: Value to return if conversion fails
    :return: Converted value or default
    """
    try:
        return converter(value)
    except (ValueError, TypeError):
        return default


@overload
def int_or_none(v: Any, default: None = ...) -> int | None: ...
@overload
def int_or_none[T](v: Any, default: T) -> int | T: ...


def int_or_none[T](v: Any, default: T | None = None) -> int | T | None:
    """Convert ``v`` to int, returning ``default`` on failure."""
    return _convert_or_none(v, int, default)


@overload
def float_or_none(v: Any, default: None = ...) -> float | None: ...
@overload
def float_or_none[T](v: Any, default: T) -> float | T: ...


def float_or_none[T](v: Any, default: T | None = None) -> float | T | None:
    """Convert ``v`` to float, returning ``default`` on failure."""
    return _convert_or_none(v, float, default)


@overload
def str_or_none(v: Any, default: None = ...) -> str | None: ...
@overload
def str_or_none[T](v: Any, default: T) -> str | T: ...


def str_or_none[T](v: Any, default: T | None = None) -> str | T | None:
    """Convert ``v`` to str, returning ``default`` on failure."""
    return _convert_or_none(v, str, default)


def attempts(max_attempts: int) -> range:
    """Return a 1-indexed range of attempt numbers up to ``max_attempts``."""
    return range(1, max_attempts + 1)


def backoff_seconds(
    attempt_number: int, retry_timeout: float | None = None
) -> float:
    """Return sleep duration for the given attempt (1-indexed).

    When *retry_timeout* is ``None`` the formula is exponential back-off
    (0 s, 1 s, 2 s, 4 s, …) plus a small random jitter of up to 0.5 s.
    The jitter prevents simultaneous retry storms when multiple processes or
    threads encounter the same transient error at the same time.
    When *retry_timeout* is a non-negative number it is used as a fixed wait.

    :param attempt_number: Current attempt number (1-indexed).
    :param retry_timeout: Fixed wait in seconds, or ``None`` for exponential
        back-off.
    :return: Seconds to sleep before the next attempt.
    """
    if retry_timeout is None:
        base = 0.0 if attempt_number <= 1 else 2.0 ** (attempt_number - 2)
        return base + _SYSTEM_RANDOM.uniform(0, 0.5)
    return float(retry_timeout)

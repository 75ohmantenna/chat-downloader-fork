# SPDX-License-Identifier: MIT

"""Defer cooperative CLI interrupts across record persistence and accounting."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_DEFERRED: ContextVar[bool] = ContextVar("deferred_interrupt", default=False)
_PENDING: ContextVar[bool] = ContextVar("pending_interrupt", default=False)


def raise_or_defer_interrupt() -> None:
    """Request the first CLI interrupt, raising outside a record boundary."""
    if _DEFERRED.get():
        _PENDING.set(True)
    else:
        raise KeyboardInterrupt


@contextmanager
def defer_interrupts() -> Iterator[None]:
    """Finish the current record before raising a requested CLI interrupt.

    Nested boundaries share one pending interrupt. An unrelated failure keeps
    priority and clears pending state when the outer boundary unwinds.
    """
    outer = _DEFERRED.get()
    token = _DEFERRED.set(True)
    try:
        yield
    except BaseException as error:
        if not outer:
            pending = _PENDING.get()
            _PENDING.set(False)
            if pending and isinstance(error, StopIteration):
                raise KeyboardInterrupt from None
        raise
    finally:
        _DEFERRED.reset(token)
    if not outer and _PENDING.get():
        _PENDING.set(False)
        raise KeyboardInterrupt


@contextmanager
def interruptible() -> Iterator[None]:
    """Allow immediate cancellation while fetching the next provider record."""
    token = _DEFERRED.set(False)
    try:
        yield
    finally:
        _DEFERRED.reset(token)

# SPDX-License-Identifier: MIT

"""Defer cooperative CLI interrupts across record and shutdown boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_DEFERRED: ContextVar[bool] = ContextVar("deferred_interrupt", default=False)
_PENDING: ContextVar[bool] = ContextVar("pending_interrupt", default=False)
_ON_INTERRUPT: ContextVar[Callable[[], None] | None] = ContextVar(
    "deferred_interrupt_callback", default=None
)


def raise_or_defer_interrupt() -> None:
    """Request the first CLI interrupt, raising outside an owned boundary."""
    if _DEFERRED.get():
        _PENDING.set(True)
        callback = _ON_INTERRUPT.get()
        if callback is not None:
            _PENDING.set(False)
            callback()
    else:
        raise KeyboardInterrupt


@contextmanager
def defer_interrupts(
    *, on_interrupt: Callable[[], None] | None = None
) -> Iterator[None]:
    """Finish an owned boundary before acting on a requested CLI interrupt.

    Nested boundaries share one pending interrupt. An unrelated failure keeps
    priority and clears pending state when the outer boundary unwinds.
    Shutdown owners may handle the request synchronously with ``on_interrupt``
    instead of raising at the boundary; second CLI signals still raise directly.
    """
    outer = _DEFERRED.get()
    token = _DEFERRED.set(True)
    callback_token = None
    try:
        callback_token = (
            _ON_INTERRUPT.set(on_interrupt) if on_interrupt is not None else None
        )
        # A signal may arrive after deferral is enabled but before its shutdown
        # observer is installed. Consume that request before protected work can
        # snapshot an apparently successful outcome.
        if on_interrupt is not None and _PENDING.get():
            _PENDING.set(False)
            on_interrupt()
        yield
    except BaseException as error:
        if not outer:
            pending = _PENDING.get()
            _PENDING.set(False)
            if pending and on_interrupt is None and isinstance(error, StopIteration):
                raise KeyboardInterrupt from None
        raise
    finally:
        _DEFERRED.reset(token)
        if callback_token is not None:
            _ON_INTERRUPT.reset(callback_token)
    if not outer and _PENDING.get():
        _PENDING.set(False)
        if on_interrupt is None:
            raise KeyboardInterrupt


@contextmanager
def interruptible() -> Iterator[None]:
    """Allow immediate cancellation while fetching the next provider record."""
    token = _DEFERRED.set(False)
    try:
        yield
    finally:
        _DEFERRED.reset(token)

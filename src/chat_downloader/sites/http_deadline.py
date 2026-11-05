# SPDX-License-Identifier: MIT

"""Monotonic HTTP completion budgets and bounded socket consumption."""

from __future__ import annotations

import io
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import TYPE_CHECKING

from requests.exceptions import Timeout as RequestTimeout
from urllib3.util import Timeout

from chat_downloader.errors import ParsingError
from chat_downloader.utils.timed_generator import (
    _check_polling_cancelled,
    _PollingCancelled,
)

if TYPE_CHECKING:
    import socket
    from collections.abc import Iterator
    from typing import BinaryIO

    from _typeshed import WriteableBuffer

READ_QUANTUM = 0.1
MAX_WIRE_BYTES = 32 * 1024 * 1024
_UNBOUNDED_TIMEOUT_BUDGET = 120.0
_REQUEST_BUDGET: ContextVar[_RequestBudget] = ContextVar("http_request_budget")


class _RequestDeadlineExceeded(Exception):
    """Unwind urllib3 without its OSError-to-ProtocolError timeout wrapping."""


class _RequestBudget:
    def __init__(self, timeout: object) -> None:
        limits = (
            Timeout.from_float(timeout)
            if isinstance(timeout, (int, float))
            else timeout
        )
        if isinstance(limits, Timeout) and isinstance(limits.total, (int, float)):
            self.deadline = monotonic() + limits.total
            return
        if isinstance(limits, tuple):
            connect, read = limits
        elif isinstance(limits, Timeout):
            connect, read = limits.connect_timeout, limits.read_timeout
        else:
            connect = read = None
        self.deadline = monotonic() + sum(
            float(value)
            if isinstance(value, (int, float))
            else _UNBOUNDED_TIMEOUT_BUDGET
            for value in (connect, read)
        )

    def remaining(self) -> float:
        _check_polling_cancelled()
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise _RequestDeadlineExceeded
        return remaining


def current_budget() -> _RequestBudget:
    """Return the active synchronous request's completion and cancellation budget."""
    return _REQUEST_BUDGET.get()


@contextmanager
def request_budget(timeout: object) -> Iterator[None]:
    """Share one completion deadline across session hooks and redirect requests."""
    current = _REQUEST_BUDGET.get(None)
    if current is not None:
        current.remaining()
        yield
        current.remaining()
        return
    budget = _RequestBudget(timeout)
    token = _REQUEST_BUDGET.set(budget)
    try:
        budget.remaining()
        yield
        budget.remaining()
    except _RequestDeadlineExceeded:
        msg = "Provider HTTP request completion deadline exceeded."
        raise RequestTimeout(msg) from None
    except _PollingCancelled:
        # Pool cancellation also inherits HTTPError so urllib3 can account for
        # an unacquired token. Restore the BaseException-only public unwind.
        raise _PollingCancelled from None
    finally:
        _REQUEST_BUDGET.reset(token)


class DeadlineReader(io.RawIOBase):
    """Check quotas and deadlines inside headers, framing and decompressor reads."""

    def __init__(self, sock: socket.socket, *, owner: BinaryIO | None = None) -> None:
        """Retain socket ownership and bind this read stream to its request."""
        super().__init__()
        self._socket = sock
        # The ownership reference also protects HTTP/1.0 responses after their
        # connection has closed, without using SocketIO's timeout-poisoned read.
        self._owner = owner if owner is not None else sock.makefile("rb", buffering=0)
        self._budget = current_budget()
        self._timeout = sock.gettimeout()
        self._received = 0

    def readable(self) -> bool:
        """Allow buffered readers to consume this socket stream."""
        return True

    def readinto(self, buffer: WriteableBuffer) -> int:
        """Receive bounded bytes without replacing the configured idle timeout."""
        idle_deadline = (
            monotonic() + self._timeout if self._timeout is not None else None
        )
        try:
            while True:
                remaining = self._budget.remaining()
                if idle_deadline is not None:
                    idle_remaining = idle_deadline - monotonic()
                    if idle_remaining <= 0:
                        msg = "Provider HTTP read timed out."
                        raise TimeoutError(msg)
                    remaining = min(remaining, idle_remaining)
                self._socket.settimeout(min(READ_QUANTUM, remaining))
                try:
                    size = self._socket.recv_into(buffer)
                except TimeoutError:
                    continue
                self._received += size
                if self._received > MAX_WIRE_BYTES:
                    msg = "Provider HTTP response exceeds the 32 MiB wire limit."
                    raise ParsingError(msg)
                return size
        finally:
            self._socket.settimeout(self._timeout)

    def close(self) -> None:
        """Release the makefile reference even after cancellation or quota failure."""
        try:
            self._owner.close()
        finally:
            super().close()

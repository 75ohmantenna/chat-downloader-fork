# SPDX-License-Identifier: MIT

"""Cooperative urllib3 retry sleeps and pooled-connection acquisition."""

from __future__ import annotations

from copy import copy
from time import monotonic, sleep
from typing import TYPE_CHECKING, cast
from weakref import ref

from urllib3.connection import HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import EmptyPoolError
from urllib3.util import Retry

from chat_downloader.errors import ParsingError
from chat_downloader.utils.timed_generator import _PollingCancelled

from .http_deadline import (
    READ_QUANTUM,
    _RequestDeadlineExceeded,
    current_budget,
)

if TYPE_CHECKING:
    from typing import Protocol

    from urllib3._base_connection import BaseHTTPConnection
    from urllib3.response import BaseHTTPResponse, HTTPResponse

    class _ConnectionWriter(Protocol):
        def __call__(self, data: object) -> None: ...


class _PoolDeadlineExceeded(EmptyPoolError, _RequestDeadlineExceeded):
    """Tell urllib3 no pool token was acquired before translating the deadline."""


class _PoolPollingCancelled(EmptyPoolError, _PollingCancelled):
    """Avoid returning a fictitious pool token when a waiting worker stops."""


def _wait(seconds: float) -> None:
    deadline = monotonic() + seconds
    while True:
        remaining = current_budget().remaining()
        delay = deadline - monotonic()
        if delay <= 0:
            return
        sleep(min(READ_QUANTUM, remaining, delay))


class _DeadlineRetry(Retry):
    def sleep_for_retry(self, response: BaseHTTPResponse) -> bool:
        """Retain Retry-After policy while polling its wait cooperatively."""
        retry_after = self.get_retry_after(response)
        if retry_after:
            _wait(retry_after)
            return True
        return False

    def _sleep_backoff(self) -> None:
        _wait(self.get_backoff_time())


def configure_retry(retry: Retry) -> Retry:
    """Copy supported retry state without mutating caller-shared Retry objects."""
    if type(retry) is _DeadlineRetry:
        return retry
    if type(retry) is not Retry:
        msg = "Unsupported custom HTTP retry policy for bounded requests."
        raise ParsingError(msg)
    if retry.total == 0:
        return retry
    bounded = copy(retry)
    bounded.__class__ = _DeadlineRetry
    return bounded


class DeadlinePoolMixin(HTTPConnectionPool):
    """Retain pool size/blocking settings without unbounded queue acquisition."""

    def _get_conn(self, timeout: float | None = None) -> BaseHTTPConnection:
        try:
            current_budget().remaining()
            if not self.block:
                return super()._get_conn(timeout=timeout)
            deadline = monotonic() + timeout if timeout is not None else None
            while True:
                quantum = min(READ_QUANTUM, current_budget().remaining())
                if deadline is not None:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        msg = "Provider HTTP connection pool acquisition timed out."
                        raise EmptyPoolError(self, msg)
                    quantum = min(quantum, remaining)
                try:
                    return super()._get_conn(timeout=quantum)
                except EmptyPoolError:
                    continue
        except _RequestDeadlineExceeded:
            msg = "Provider HTTP request completion deadline exceeded."
            raise _PoolDeadlineExceeded(self, msg) from None
        except _PollingCancelled:
            msg = "Provider HTTP connection pool acquisition cancelled."
            raise _PoolPollingCancelled(self, msg) from None


class DeadlineConnectionMixin(HTTPConnection):
    """Refresh I/O budgets after retries and pool waits, before socket use."""

    def connect(self) -> None:
        """Clamp connect I/O to the budget left after acquiring the connection."""
        remaining = current_budget().remaining()
        connect_timeout = (
            min(self.timeout, remaining)
            if isinstance(self.timeout, (int, float))
            else remaining
        )
        self.timeout = connect_timeout
        super().connect()
        remaining = current_budget().remaining()
        if self.sock is not None:
            self.sock.settimeout(min(connect_timeout, remaining))

    def send(self, data: object) -> None:
        """Retain socket timeout semantics within the current completion budget."""
        remaining = current_budget().remaining()
        writer = cast("_ConnectionWriter", super().send)
        if self.sock is None:
            writer(data)
            current_budget().remaining()
            return
        sock = self.sock
        timeout = sock.gettimeout()
        try:
            sock.settimeout(
                min(timeout, remaining) if timeout is not None else remaining
            )
            writer(data)
            current_budget().remaining()
        finally:
            sock.settimeout(timeout)

    # urllib3 deliberately changes the stdlib response type on this interface.
    def getresponse(self) -> HTTPResponse:  # type: ignore[override]
        """Close ignored retry bodies instead of allocating or draining them."""
        response = super().getresponse()
        response_ref = ref(response)

        def discard() -> None:
            current = response_ref()
            if current is not None:
                current.close()
                current.release_conn()

        response.drain_conn = discard  # type: ignore[method-assign]
        return response

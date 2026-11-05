# SPDX-License-Identifier: MIT

"""Cooperative receive boundaries underneath urllib3's TLS-in-TLS loop."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, cast

from urllib3.connection import HTTPSConnection

from .http_deadline import READ_QUANTUM, current_budget

if TYPE_CHECKING:
    import socket
    import ssl


class _ProxyDeadlineSocket:
    """Delegate the proxy transport, checking each inner TLS network receive.

    Wrapping SSLTransport.recv_into is too late: its MemoryBIO loop can receive
    indefinitely without producing any application bytes. Wrap its underlying
    proxy socket instead, without replacing the proxy's TLS implementation.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def __getattr__(self, name: str) -> object:
        return getattr(self._socket, name)

    @property
    def _io_refs(self) -> int:
        # SSLTransport.makefile increments this attribute on its socket. Forward
        # writes too, so SocketIO.close decrements the same ownership counter.
        return cast("int", self._socket._io_refs)  # type: ignore[attr-defined]

    @_io_refs.setter
    def _io_refs(self, value: int) -> None:
        self._socket._io_refs = value  # type: ignore[attr-defined]

    def recv(self, size: int, flags: int = 0) -> bytes:
        """Poll a receive without shortening the configured inactivity timeout."""
        timeout = self._socket.gettimeout()
        idle_deadline = monotonic() + timeout if timeout is not None else None
        try:
            budget = current_budget()
            while True:
                remaining = budget.remaining()
                if timeout == 0:
                    return self._socket.recv(size, flags)
                if idle_deadline is not None:
                    idle_remaining = idle_deadline - monotonic()
                    if idle_remaining <= 0:
                        msg = "Provider HTTPS proxy read timed out."
                        raise TimeoutError(msg)
                    remaining = min(remaining, idle_remaining)
                self._socket.settimeout(min(READ_QUANTUM, remaining))
                try:
                    return self._socket.recv(size, flags)
                except TimeoutError:
                    # Retry the polling quantum, not the caller's idle timeout.
                    continue
        finally:
            self._socket.settimeout(timeout)


class HTTPSProxyDeadlineMixin(HTTPSConnection):
    """Compose before an HTTPS connection class to bound nested TLS receives."""

    def _connect_tls_proxy(self, hostname: str, sock: socket.socket) -> ssl.SSLSocket:
        # Keep the superclass's proxy context, certificate policy, verification
        # state and custom TLS hooks. Only add a receive boundary to its result.
        proxy_socket = super()._connect_tls_proxy(hostname, sock)
        return cast("ssl.SSLSocket", _ProxyDeadlineSocket(proxy_socket))

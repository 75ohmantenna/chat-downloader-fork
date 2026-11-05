# SPDX-License-Identifier: MIT

"""Connection-local SOCKS negotiation with cooperative request deadlines."""

from __future__ import annotations

import errno
import io
import select
import socket
from time import monotonic
from typing import TYPE_CHECKING, Protocol, cast

import requests
import socks  # type: ignore[import-untyped]  # PySocks ships without type stubs
from urllib3.contrib.socks import SOCKSConnection
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from .http_deadline import READ_QUANTUM, DeadlineReader, current_budget

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO, TextIO

    from _typeshed import ReadableBuffer


class _ProxyErrorDetails(Protocol):
    socket_err: OSError | None


class _SocketMakefile(Protocol):
    def __call__(
        self,
        mode: str = "r",
        buffering: int = -1,
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> BinaryIO | TextIO: ...


if TYPE_CHECKING:

    class _SOCKSSocket(socket.socket):
        """The PySocks API used here; the runtime base remains socks.socksocket."""

        proxy: tuple[
            int | None,
            str | None,
            int | str | None,
            bool | None,
            bytes | None,
            bytes | None,
        ]
        _proxy_negotiators: dict[int | None, Callable[[_SOCKSSocket, str, int], None]]

        def set_proxy(
            self,
            proxy_type: int | None = None,
            addr: str | None = None,
            port: int | str | None = None,
            rdns: bool = True,  # noqa: FBT001, FBT002
            username: str | None = None,
            password: str | None = None,
        ) -> None: ...
else:
    _SOCKSSocket = socks.socksocket

_Address = tuple[str, int] | tuple[str, int, int, int]
_PENDING = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR}


def _remaining(idle_deadline: float | None) -> float:
    remaining = current_budget().remaining()
    if idle_deadline is not None:
        remaining = min(remaining, idle_deadline - monotonic())
        if remaining <= 0:
            msg = "Provider SOCKS connection timed out."
            raise TimeoutError(msg)
    return min(READ_QUANTUM, remaining)


class _DeadlineSOCKSSocket(_SOCKSSocket):
    # Binary negotiation streams wrap SocketIO in DeadlineReader rather than
    # preserving socket.makefile's concrete wrapper-specific overload returns.
    def makefile(  # type: ignore[override]
        self,
        mode: str = "r",
        buffering: int = -1,
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> BinaryIO | TextIO:
        """Keep PySocks reads inside the shared budget without recursion."""
        if mode == "rb":
            owner = cast("BinaryIO", super().makefile("rb", 0))
            try:
                reader = DeadlineReader(self, owner=owner)
                if buffering == 0:
                    return cast("BinaryIO", reader)
                size = io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering
                return io.BufferedReader(reader, size)
            except BaseException:
                owner.close()
                raise
        return cast("_SocketMakefile", super().makefile)(
            mode, buffering, encoding=encoding, errors=errors, newline=newline
        )

    def send(self, data: ReadableBuffer, flags: int = 0) -> int:
        """Bound negotiation writes as well as its buffered binary reads."""
        timeout = self.gettimeout()
        idle_deadline = monotonic() + timeout if timeout is not None else None
        try:
            while True:
                self.settimeout(_remaining(idle_deadline))
                try:
                    return super().send(data, flags)
                except TimeoutError:
                    continue
        finally:
            self.settimeout(timeout)

    def connect_proxy(self, address: _Address, destination: tuple[str, int]) -> None:
        """Connect to one resolved proxy address, then reuse PySocks protocols."""
        timeout = self.gettimeout()
        idle_deadline = monotonic() + timeout if timeout is not None else None
        try:
            _remaining(idle_deadline)
            socket.socket.settimeout(self, 0)
            result = socket.socket.connect_ex(self, address)
            while result:
                if result not in _PENDING:
                    msg = "Unable to connect to provider SOCKS proxy."
                    raise OSError(result, msg)
                quantum = _remaining(idle_deadline)
                _, writable, exceptional = select.select([], [self], [self], quantum)
                if writable or exceptional:
                    result = self.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            current_budget().remaining()
        finally:
            socket.socket.settimeout(self, timeout)
        # Retain the configured connect timeout as the negotiation idle timeout.
        # The reader imposes a separate monotonic completion deadline.
        proxy_type = self.proxy[0]
        negotiate = self._proxy_negotiators[proxy_type]
        negotiate(self, *destination)
        current_budget().remaining()


class SOCKSDeadlineMixin(SOCKSConnection):
    """Compose before an existing SOCKS connection without global patching."""

    def _new_conn(self) -> socket.socket:
        try:
            return self._connect_socks()
        except requests.exceptions.Timeout:
            raise
        except OSError as error:
            cause = (
                cast("_ProxyErrorDetails", error).socket_err
                if isinstance(error, socks.ProxyError)
                else error
            )
            if isinstance(cause, socket.timeout):
                msg = "Provider SOCKS connection timed out."
                raise ConnectTimeoutError(self, msg) from None
            msg = "Unable to establish provider SOCKS connection."
            raise NewConnectionError(self, msg) from None
        except ValueError:
            msg = "Unable to establish provider SOCKS connection."
            raise NewConnectionError(self, msg) from None

    def _connect_socks(self) -> socket.socket:
        options = self._socks_options
        proxy_host = options["proxy_host"]
        if proxy_host is not None:
            proxy_host = proxy_host.strip("[]")
        destination = (self.host.strip("[]"), self.port)
        current_budget().remaining()
        addresses = socket.getaddrinfo(
            proxy_host, options["proxy_port"], 0, socket.SOCK_STREAM
        )
        current_budget().remaining()
        last_error: OSError | None = None
        for family, kind, protocol, _, address in addresses:
            current_budget().remaining()
            try:
                conn = self._open_proxy_socket(
                    family,
                    kind,
                    protocol,
                    cast("_Address", address),
                    destination,
                    proxy_host,
                )
            except requests.exceptions.Timeout:
                raise
            except OSError as error:
                last_error = error
                continue
            return conn
        if last_error is not None:
            raise last_error
        msg = "Provider SOCKS proxy has no usable address."
        raise OSError(msg)

    def _open_proxy_socket(
        self,
        family: int,
        kind: int,
        protocol: int,
        address: _Address,
        destination: tuple[str, int],
        proxy_host: str | None,
    ) -> _DeadlineSOCKSSocket:
        """Own one proxy socket through setup, negotiation and cancellation."""
        conn = _DeadlineSOCKSSocket(family, kind, protocol)
        try:
            if self.socket_options:
                for option in self.socket_options:
                    conn.setsockopt(*option)
            if isinstance(self.timeout, (int, float)):
                conn.settimeout(self.timeout)
            options = self._socks_options
            conn.set_proxy(
                options["socks_version"],
                proxy_host,
                options["proxy_port"],
                options["rdns"],
                options["username"],
                options["password"],
            )
            if self.source_address:
                conn.bind(self.source_address)
            conn.connect_proxy(address, destination)
        except BaseException:
            conn.close()
            raise
        return conn

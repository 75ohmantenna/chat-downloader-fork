# SPDX-License-Identifier: MIT

"""Provider-neutral bounded response consumption for requests sessions."""

from __future__ import annotations

import http.client
import io
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin, urlsplit
from weakref import WeakKeyDictionary, ref

import requests
from requests.adapters import BaseAdapter, HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.contrib.socks import (
    SOCKSConnection,
    SOCKSHTTPConnectionPool,
    SOCKSHTTPSConnectionPool,
    SOCKSProxyManager,
)
from urllib3.exceptions import HTTPError
from urllib3.poolmanager import PoolManager, ProxyManager
from urllib3.util import Timeout

from chat_downloader.errors import ParsingError
from chat_downloader.sites.http_deadline import (
    DeadlineReader,
    current_budget,
    request_budget,
)
from chat_downloader.sites.http_socks import SOCKSDeadlineMixin
from chat_downloader.sites.http_tls import HTTPSProxyDeadlineMixin
from chat_downloader.sites.http_waits import (
    DeadlineConnectionMixin,
    DeadlinePoolMixin,
    configure_retry,
)

if TYPE_CHECKING:
    import socket
    from typing import Protocol
    from weakref import ReferenceType

    from urllib3.response import HTTPResponse

    class _TimedAdapterSender(Protocol):
        def __call__(
            self,
            request: requests.PreparedRequest,
            *,
            timeout: Timeout,
            **kwargs: object,
        ) -> requests.Response: ...


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_READ_BYTES = 64 * 1024
_ACTIVE_SESSION: ContextVar[requests.Session | None] = ContextVar(
    "bounded_http_session", default=None
)


class _ResponseSocket:
    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def makefile(self, _mode: str) -> io.BufferedReader:
        """Supply http.client with a deadline-aware buffered reader."""
        return io.BufferedReader(DeadlineReader(self._socket))


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(
        self,
        sock: socket.socket,
        debuglevel: int = 0,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(
            cast("socket.socket", _ResponseSocket(sock)), debuglevel, method, url
        )


# Built-in pool classes are a fixed vocabulary; custom classes have weak keys
# AND weak values because a generated subclass itself retains its original base.
_FIXED_POOL_TYPES: dict[type[HTTPConnectionPool], type[HTTPConnectionPool]] = {}
_BUILTIN_POOL_CLASSES = frozenset(
    {
        HTTPConnectionPool,
        HTTPSConnectionPool,
        SOCKSHTTPConnectionPool,
        SOCKSHTTPSConnectionPool,
    }
)
_POOL_TYPES: WeakKeyDictionary[
    type[HTTPConnectionPool], ReferenceType[type[HTTPConnectionPool]]
] = WeakKeyDictionary()


def _require_pool_io(pool: type[HTTPConnectionPool]) -> None:
    """Require the library wait hooks that the deadline mixin can bound."""
    if (
        pool._get_conn is not HTTPConnectionPool._get_conn
        or pool.urlopen is not HTTPConnectionPool.urlopen
        or pool._make_request is not HTTPConnectionPool._make_request
    ):
        msg = "Unsupported custom HTTP pool acquisition or request policy."
        raise ParsingError(msg)


def _pool_connection_class(pool: type[HTTPConnectionPool]) -> type[HTTPConnection]:
    """Require library socket I/O while retaining supported TLS configuration."""
    _require_pool_io(pool)
    connection = pool.ConnectionCls
    if (
        not isinstance(connection, type)
        or not issubclass(connection, HTTPConnection)
        or connection.getresponse is not HTTPConnection.getresponse
        or connection.request is not HTTPConnection.request
        or connection.send is not HTTPConnection.send
        or connection.connect not in {HTTPConnection.connect, HTTPSConnection.connect}
        or connection._new_conn
        not in {HTTPConnection._new_conn, SOCKSConnection._new_conn}
    ):
        msg = "Unsupported custom HTTP connection for bounded requests."
        raise ParsingError(msg)
    return connection


def _bounded_pool(pool: type[HTTPConnectionPool]) -> type[HTTPConnectionPool]:
    fixed = _FIXED_POOL_TYPES.get(pool)
    if fixed is not None:
        return fixed
    cached_ref = _POOL_TYPES.get(pool)
    cached = cached_ref() if cached_ref is not None else None
    if cached is not None:
        return cached
    connection = _pool_connection_class(pool)
    bases: list[type[HTTPConnection]] = [DeadlineConnectionMixin]
    if issubclass(connection, SOCKSConnection):
        bases.append(SOCKSDeadlineMixin)
    if issubclass(connection, HTTPSConnection) and not issubclass(
        connection, HTTPSProxyDeadlineMixin
    ):
        bases.append(HTTPSProxyDeadlineMixin)
    bases.append(connection)
    bounded_connection = type(
        f"Bounded{connection.__name__}",
        tuple(bases),
        {"response_class": _DeadlineResponse},
    )
    bounded_pool = type(
        f"Bounded{pool.__name__}",
        (DeadlinePoolMixin, pool),
        {"ConnectionCls": bounded_connection},
    )
    if pool in _BUILTIN_POOL_CLASSES:
        _FIXED_POOL_TYPES[pool] = bounded_pool
    else:
        _POOL_TYPES[pool] = ref(bounded_pool)
    _POOL_TYPES[bounded_pool] = ref(bounded_pool)
    return bounded_pool


def _configure_manager(manager: PoolManager) -> None:
    if type(manager) not in {PoolManager, ProxyManager, SOCKSProxyManager}:
        msg = "Unsupported custom HTTP pool manager for bounded requests."
        raise ParsingError(msg)
    pools = manager.pool_classes_by_scheme
    bounded = {scheme: _bounded_pool(pool) for scheme, pool in pools.items()}
    if any(bounded[scheme] is not pool for scheme, pool in pools.items()):
        manager.clear()
        manager.pool_classes_by_scheme = bounded


class _BoundedAdapter(HTTPAdapter):
    def add_headers(self, request: requests.PreparedRequest, **_kwargs: object) -> None:
        """Keep bounded decoding negotiation independent of session headers."""
        super().add_headers(request, **_kwargs)
        request.headers.setdefault("Accept-Encoding", "gzip, deflate")

    def send(
        self,
        request: requests.PreparedRequest,
        # Requests' adapter interface permits a positional stream argument.
        stream: bool = False,  # noqa: FBT001, FBT002
        timeout: object = None,
        verify: bool | str = True,  # noqa: FBT001, FBT002 - Requests adapter signature
        cert: str | tuple[str, str] | None = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        """Own a completion budget even for direct underlying-session calls."""
        with request_budget(timeout):
            if isinstance(timeout, tuple):
                bounded = Timeout(connect=timeout[0], read=timeout[1])
            elif isinstance(timeout, Timeout):
                bounded = timeout.clone()
            else:
                bounded = Timeout.from_float(cast("float | None", timeout))
            remaining = current_budget().remaining()
            bounded.total = (
                min(bounded.total, remaining)
                if isinstance(bounded.total, (float, int))
                else remaining
            )
            # Requests accepts urllib3 Timeout objects at this runtime boundary.
            send = cast("_TimedAdapterSender", super().send)
            return send(
                request,
                stream=stream,
                timeout=bounded,
                verify=verify,
                cert=cert,
                proxies=proxies,
            )

    def build_response(
        self, req: requests.PreparedRequest, resp: HTTPResponse
    ) -> requests.Response:
        """Consume and release the body before requests dispatches user hooks."""
        response = super().build_response(req, resp)
        try:
            return _buffer_response(response)
        except BaseException:
            # Requests normally extracts cookies before consuming the body.
            # Preserve that ordering's result when bounded consumption fails.
            session = _ACTIVE_SESSION.get()
            if session is not None:
                extract_cookies_to_jar(session.cookies, req, resp)
            raise

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: object) -> PoolManager:
        """Keep HTTP and SOCKS proxy readers bounded with stable pool types."""
        manager = cast("PoolManager", super().proxy_manager_for(proxy, **proxy_kwargs))
        _configure_manager(manager)
        return manager


def _configure_adapter(adapter: BaseAdapter) -> None:
    """Preserve supported adapter configuration and reject unenforceable transports."""
    bounded = isinstance(adapter, _BoundedAdapter)
    sender, builder = (
        (_BoundedAdapter.send, _BoundedAdapter.build_response)
        if bounded
        else (HTTPAdapter.send, HTTPAdapter.build_response)
    )
    if (
        not isinstance(adapter, HTTPAdapter)
        or getattr(adapter.send, "__func__", None) is not sender
        or getattr(adapter.build_response, "__func__", None) is not builder
        or getattr(adapter.get_connection_with_tls_context, "__func__", None)
        is not HTTPAdapter.get_connection_with_tls_context
    ):
        msg = "Unsupported custom HTTP adapter for bounded requests."
        raise ParsingError(msg)
    adapter.max_retries = configure_retry(adapter.max_retries)
    _configure_manager(adapter.poolmanager)
    for manager in adapter.proxy_manager.values():
        _configure_manager(cast("PoolManager", manager))
    if bounded:
        return
    original_class = type(adapter)
    try:
        adapter.__class__ = (
            _BoundedAdapter
            if original_class is HTTPAdapter
            else type(
                f"Bounded{original_class.__name__}",
                (_BoundedAdapter, original_class),
                {},
            )
        )
    except TypeError:
        msg = "Unable to enforce bounded requests on custom HTTP adapter."
        raise ParsingError(msg) from None


def https_origin(url: str) -> tuple[str, int]:
    """Require a credential-free HTTPS URL before a redirect request."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return parsed.hostname, parsed.port or 443
    except ValueError:
        pass
    msg = "Unsafe HTTP redirect destination."
    raise ParsingError(msg)


def redirect_destination(
    response: requests.Response, url: str, origin: tuple[str, int] | None
) -> str:
    """Validate a bounded same-origin redirect before sending credentials."""
    try:
        location = response.headers.get("Location", "")
        destination = urljoin(url, location)
    except ValueError:
        msg = "Unsafe HTTP redirect destination."
        raise ParsingError(msg) from None
    if not location:
        msg = "Unexpected HTTP redirect from provider."
        raise ParsingError(msg)
    if https_origin(destination) != origin:
        msg = "Cross-origin HTTP redirect from provider."
        raise ParsingError(msg)
    return destination


def _buffer_response(response: requests.Response) -> requests.Response:
    """Materialize only a bounded decoded body, always releasing its response."""
    try:
        budget = current_budget()
        budget.remaining()
        if 300 <= response.status_code < 400:
            # requests prepares Response.next even with allow_redirects=False,
            # consuming the body in resolve_redirects. Close before that path.
            content = b""
            response.raw.close()
        else:
            encoding = (
                response.headers.get("Content-Encoding", "").strip().lower()
                or "identity"
            )
            if encoding not in {"identity", "gzip", "x-gzip", "deflate"}:
                msg = "Unsupported provider HTTP content encoding."
                raise ParsingError(msg)
            content_buffer = bytearray()
            while True:
                budget.remaining()
                chunk = response.raw.read1(
                    min(_READ_BYTES, MAX_RESPONSE_BYTES - len(content_buffer) + 1),
                    decode_content=True,
                )
                budget.remaining()
                if not chunk:
                    break
                if len(chunk) > MAX_RESPONSE_BYTES - len(content_buffer):
                    msg = "Provider HTTP decoded response exceeds the 16 MiB limit."
                    raise ParsingError(msg)
                content_buffer.extend(chunk)
            content = bytes(content_buffer)
        response._content = content
        response._content_consumed = True
    except HTTPError:
        msg = "Unable to read provider HTTP response."
        raise requests.exceptions.ConnectionError(msg) from None
    finally:
        response.close()
    return response


def configure_bounded_session(session: requests.Session) -> None:
    """Install bounded streaming before requests can access response content."""
    # Owned transport waits cooperate; arbitrary user callbacks are not preempted.
    if getattr(session.send, "__func__", None) is not requests.sessions.Session.send:
        msg = "Unsupported custom HTTP session sender for bounded requests."
        raise ParsingError(msg)
    for adapter in set(session.adapters.values()):
        _configure_adapter(adapter)
    original_send = session.send

    def send(request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        with request_budget(kwargs.get("timeout")):
            _configure_adapter(session.get_adapter(cast("str", request.url)))
            token = _ACTIVE_SESSION.set(session)
            try:
                return original_send(request, **kwargs)
            finally:
                _ACTIVE_SESSION.reset(token)

    # This is one owned instance; the original Requests lifecycle remains intact.
    session.send = send  # type: ignore[method-assign]

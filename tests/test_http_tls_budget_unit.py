# SPDX-License-Identifier: MIT

from __future__ import annotations

import socket
import ssl
import threading
import time
from contextlib import suppress
from socketserver import BaseRequestHandler, ThreadingTCPServer
from types import SimpleNamespace

import pytest
import requests
from urllib3 import ProxyManager
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import ProtocolError
from urllib3.util import Timeout

from chat_downloader.sites import http_tls
from chat_downloader.sites.http_deadline import request_budget
from chat_downloader.utils.timed_generator import TimedGenerator, _PollingCancelled
from tests.test_http_policy_unit import TLS_FIXTURES
from tests.test_http_policy_unit import local_http as _local_http  # noqa: F401 - named fixture


def _manager(proxy_url):
    calls = []

    class CustomHTTPSConnection(HTTPSConnection):
        def _connect_tls_proxy(self, hostname, sock):
            result = super()._connect_tls_proxy(hostname, sock)
            calls.append(result)
            return result

    connection = type(
        "DeadlineHTTPSConnection",
        (http_tls.HTTPSProxyDeadlineMixin, CustomHTTPSConnection),
        {},
    )
    pool = type(
        "DeadlineHTTPSPool", (HTTPSConnectionPool,), {"ConnectionCls": connection}
    )
    context = ssl.create_default_context(cafile=str(TLS_FIXTURES / "certificate.pem"))
    manager = ProxyManager(
        proxy_url,
        proxy_ssl_context=context,
        ssl_context=context,
        timeout=Timeout(connect=2, read=2),
        retries=False,
    )
    manager.pool_classes_by_scheme = {
        **manager.pool_classes_by_scheme,
        "https": pool,
    }
    return manager, calls


@pytest.fixture
def fragmented_tls():
    state = SimpleNamespace(
        stop=threading.Event(),
        fragmented=threading.Event(),
        disconnected=threading.Event(),
        finished=threading.Event(),
        connection=None,
        mode="handshake",
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        TLS_FIXTURES / "certificate.pem",
        TLS_FIXTURES / "private-key.pem",
    )

    class Handler(BaseRequestHandler):
        def handle(self):
            state.connection = self.request
            self.request.settimeout(2)
            incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
            inner = context.wrap_bio(incoming, outgoing, server_side=True)

            def fragment(data):
                # An incomplete TLS record makes SSLTransport loop without ever
                # returning application data to the outer response reader.
                for index in range(len(data)):
                    if state.stop.is_set():
                        return
                    self.request.sendall(data[index : index + 1])
                    state.fragmented.set()
                    if state.stop.wait(0.01):
                        return

            try:
                while True:
                    try:
                        inner.do_handshake()
                        complete = True
                    except ssl.SSLWantReadError:
                        complete = False
                    flight = outgoing.read()
                    if flight and state.mode == "handshake":
                        fragment(flight)
                        return
                    self.request.sendall(flight)
                    if complete:
                        break
                    data = self.request.recv(65536)
                    if not data:
                        return
                    incoming.write(data)
                request = b""
                while b"\r\n\r\n" not in request:
                    try:
                        request += inner.read(65536)
                    except ssl.SSLWantReadError:
                        self.request.sendall(outgoing.read())
                        data = self.request.recv(65536)
                        if not data:
                            return
                        incoming.write(data)
                inner.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 12000\r\n\r\n" + b"x" * 12000,
                )
                fragment(outgoing.read())
            except OSError:
                state.disconnected.set()
            finally:
                state.finished.set()

    server = ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    state.url = f"https://127.0.0.1:{server.server_address[1]}"
    thread.start()
    try:
        yield state
    finally:
        state.stop.set()
        if state.connection is not None:
            with suppress(OSError):
                state.connection.shutdown(socket.SHUT_RDWR)
            state.connection.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        if state.connection is not None:
            assert state.finished.wait(2)
        assert not thread.is_alive()


def test_real_https_proxy_preserves_custom_tls_hook_and_ordinary_request(local_http):
    destination = local_http(tls=True)
    proxy = local_http(tls=True)
    manager, calls = _manager(proxy.url)
    try:
        with request_budget(Timeout(total=3)):
            response = manager.request("GET", destination.url)
        assert response.status == 200
        assert response.data == b'{"ok": true}'
        response.release_conn()
        assert len(calls) == 1
        assert calls[0].gettimeout() > http_tls.READ_QUANTUM
        # Exercise the delegated SocketIO ownership path again on keepalive.
        with request_budget(Timeout(total=3)):
            response = manager.request("GET", destination.url)
        assert response.data == b'{"ok": true}'
        response.release_conn()
        assert len(calls) == 1
    finally:
        manager.clear()


@pytest.mark.timeout(5)
@pytest.mark.parametrize("mode", ["handshake", "response"])
def test_fragmented_inner_tls_cannot_extend_completion_deadline(
    local_http,
    fragmented_tls,
    mode,
):
    proxy = local_http(tls=True)
    fragmented_tls.mode = mode
    manager, calls = _manager(proxy.url)
    started = time.monotonic()
    try:
        with (
            pytest.raises(
                (requests.exceptions.Timeout, ProtocolError),
                match="completion deadline",
            ),
            request_budget(Timeout(total=0.6)),
        ):
            manager.request("GET", fragmented_tls.url)
        assert time.monotonic() - started < 1.8
        assert fragmented_tls.fragmented.is_set()
        assert fragmented_tls.disconnected.wait(1)
        assert calls[0].fileno() == -1
    finally:
        manager.clear()


@pytest.mark.timeout(5)
@pytest.mark.parametrize("mode", ["handshake", "response"])
def test_timed_stop_unwinds_nested_tls_worker_and_releases_proxy(
    local_http,
    fragmented_tls,
    mode,
):
    proxy = local_http(tls=True)
    fragmented_tls.mode = mode
    manager, calls = _manager(proxy.url)
    finalized = threading.Event()

    def messages():
        try:
            with request_budget(Timeout(total=30)):
                manager.request("GET", fragmented_tls.url)
            yield "unexpected"
        finally:
            finalized.set()

    timed = TimedGenerator(messages())
    try:
        assert fragmented_tls.fragmented.wait(2)
        timed.close()
        assert finalized.wait(1)
        timed._worker.join(1)
        assert not timed._worker.is_alive()
        assert fragmented_tls.disconnected.wait(1)
        assert calls[0].fileno() == -1
    finally:
        timed.close()
        manager.clear()


class _Socket:
    def __init__(self, actions, clock):
        self.actions = iter(actions)
        self.clock = clock
        self.timeout = 0.35
        self.timeouts = []
        self._io_refs = 0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout
        self.timeouts.append(timeout)

    def recv(self, size, flags=0):
        self.clock.now += self.timeout or 0
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return action


def _wrapped(monkeypatch, actions, remaining):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(http_tls, "monotonic", lambda: clock.now)
    monkeypatch.setattr(
        http_tls,
        "current_budget",
        lambda: SimpleNamespace(remaining=remaining),
    )
    sock = _Socket(actions, clock)
    return http_tls._ProxyDeadlineSocket(sock), sock, clock


@pytest.mark.parametrize("timeout", [0.35, None])
@pytest.mark.parametrize("payload", [b"ok", b""])
def test_socket_quantum_preserves_full_inactivity_timeout_and_ownership(
    monkeypatch,
    timeout,
    payload,
):
    wrapped, sock, clock = _wrapped(
        monkeypatch,
        [TimeoutError(), TimeoutError(), payload],
        lambda: 10,
    )
    sock.timeout = timeout
    assert wrapped.recv(10) == payload
    assert clock.now == pytest.approx(0.3)
    assert sock.gettimeout() == timeout
    assert sock.timeouts[:-1] == [http_tls.READ_QUANTUM] * 3
    wrapped._io_refs += 1
    assert sock._io_refs == 1
    assert wrapped.gettimeout() == timeout


def test_socket_preserves_nonblocking_semantics(monkeypatch):
    wrapped, sock, _clock = _wrapped(
        monkeypatch,
        [BlockingIOError()],
        lambda: 10,
    )
    sock.timeout = 0
    with pytest.raises(BlockingIOError):
        wrapped.recv(10)
    assert sock.gettimeout() == 0


def test_socket_polling_does_not_reset_inactivity_timeout(monkeypatch):
    wrapped, sock, clock = _wrapped(
        monkeypatch,
        [TimeoutError()] * 4,
        lambda: 10,
    )
    with pytest.raises(socket.timeout, match="HTTPS proxy read timed out"):
        wrapped.recv(10)
    assert clock.now == pytest.approx(0.35)
    assert sock.gettimeout() == 0.35


@pytest.mark.parametrize("error", [requests.exceptions.Timeout(), _PollingCancelled()])
def test_socket_restores_timeout_when_budget_or_cancellation_interrupts(
    monkeypatch, error
):
    checks = []

    def remaining():
        checks.append(None)
        if len(checks) == 3:
            raise error
        return 10

    wrapped, sock, _clock = _wrapped(
        monkeypatch,
        [TimeoutError(), TimeoutError()],
        remaining,
    )
    with pytest.raises(type(error)):
        wrapped.recv(10)
    assert len(checks) == 3
    assert sock.gettimeout() == 0.35


def test_socket_restores_timeout_after_transport_failure(monkeypatch):
    wrapped, sock, _clock = _wrapped(
        monkeypatch,
        [ConnectionResetError("closed")],
        lambda: 10,
    )
    with pytest.raises(ConnectionResetError, match="closed"):
        wrapped.recv(10)
    assert sock.gettimeout() == 0.35

# SPDX-License-Identifier: MIT

from __future__ import annotations

import socket
import threading
import time
from contextlib import ExitStack
from socketserver import StreamRequestHandler, ThreadingTCPServer
from types import SimpleNamespace

import pytest
import requests
import socks
from urllib3.contrib.socks import SOCKSConnection, SOCKSHTTPSConnection
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util import Timeout

from chat_downloader.sites import http_socks
from chat_downloader.sites.http_deadline import DeadlineReader, request_budget
from chat_downloader.sites.http_socks import SOCKSDeadlineMixin
from chat_downloader.sites.session import ChatDownloaderSession
from chat_downloader.utils.timed_generator import TimedGenerator, _PollingCancelled
from tests.test_http_policy_unit import TLS_FIXTURES
from tests.test_http_policy_unit import local_http as _local_http  # noqa: F401 - named fixture
from tests.test_http_policy_unit import socks_proxy as _socks_proxy  # noqa: F401 - named fixture


@pytest.fixture
def negotiation_proxy():
    with ExitStack() as stack:

        def start(mode="success", *, ipv6=False):
            state = SimpleNamespace(
                stop=threading.Event(),
                negotiating=threading.Event(),
                disconnected=threading.Event(),
                destination=None,
                username=None,
            )

            class Handler(StreamRequestHandler):
                def handle(self):
                    try:
                        version = self.rfile.read(1)
                        if version == b"\x04":
                            request = self.rfile.read(7)
                            state.destination = request[3:7]
                            state.username = b""
                            while (value := self.rfile.read(1)) != b"\x00":
                                if not value:
                                    return
                                state.username += value
                            if state.destination == b"\x00\x00\x00\x01":
                                host = bytearray()
                                while (value := self.rfile.read(1)) != b"\x00":
                                    if not value:
                                        return
                                    host.extend(value)
                                state.destination = bytes(host)
                            self.wfile.write(b"\x00\x5a\x00\x00\x7f\x00\x00\x01")
                        else:
                            assert version == b"\x05"
                            count = self.rfile.read(1)[0]
                            self.rfile.read(count)
                            authenticate = mode in {"auth", "reject", "eof", "stall"}
                            self.wfile.write(
                                b"\x05\x02" if authenticate else b"\x05\x00"
                            )
                            if authenticate:
                                assert self.rfile.read(1) == b"\x01"
                                state.username = self.rfile.read(self.rfile.read(1)[0])
                                state.password = self.rfile.read(self.rfile.read(1)[0])
                                self.wfile.write(b"\x01\x00")
                            _, _, _, kind = self.rfile.read(4)
                            length = (
                                4
                                if kind == 1
                                else 16
                                if kind == 4
                                else self.rfile.read(1)[0]
                            )
                            state.destination = (kind, self.rfile.read(length))
                            self.rfile.read(2)
                            state.negotiating.set()
                            if mode == "reject":
                                self.wfile.write(b"\x05\x05\x00")
                                return
                            if mode == "eof":
                                self.wfile.write(
                                    b"\x05\x00\x00\x03\xffPROXY_REPLY_SECRET"
                                )
                                return
                            if mode == "stall":
                                state.stop.wait(3)
                                return
                            if mode == "trickle":
                                self.wfile.write(b"\x05\x00\x00\x03\xff")
                                for _ in range(255):
                                    self.wfile.write(b"x")
                                    if state.stop.wait(0.01):
                                        return
                                self.wfile.write(b"\x00\x00")
                            else:
                                self.wfile.write(
                                    b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00"
                                )
                        state.negotiating.set()
                        self.connection.settimeout(0.05)
                        while not state.stop.is_set():
                            try:
                                if not self.connection.recv(1):
                                    state.disconnected.set()
                                    return
                            except TimeoutError:
                                continue
                    except (OSError, IndexError, ValueError):
                        state.disconnected.set()

            class Server(ThreadingTCPServer):
                address_family = socket.AF_INET6 if ipv6 else socket.AF_INET
                daemon_threads = True

            server = Server(("::1" if ipv6 else "127.0.0.1", 0), Handler)
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.01},
                daemon=True,
            )
            thread.start()
            stack.callback(thread.join, 2)
            stack.callback(server.server_close)
            stack.callback(server.shutdown)
            stack.callback(state.stop.set)
            state.host, state.port = server.server_address[:2]
            state.url = f"socks5h://127.0.0.1:{state.port}"
            return state

        yield start


def _connection(
    proxy, *, version=socks.SOCKS5, rdns=True, host="private.invalid", **kwargs
):
    options = {
        "socks_version": version,
        "proxy_host": proxy.host,
        "proxy_port": proxy.port,
        "username": "synthetic-user",
        "password": "synthetic-password",
        "rdns": rdns,
    }
    return SOCKSDeadlineMixin(
        host=host, port=80, timeout=0.5, _socks_options=options, **kwargs
    )


@pytest.mark.parametrize("mode", ["trickle", "stall"])
def test_real_handshake_obeys_completion_budget_and_closes_socket(
    negotiation_proxy, mode
):
    proxy = negotiation_proxy(mode)
    connection = _connection(proxy)
    started = time.monotonic()
    with (
        pytest.raises(requests.exceptions.Timeout, match="completion deadline"),
        request_budget(Timeout(total=0.2)),
    ):
        connection._new_conn()
    assert proxy.negotiating.is_set()
    assert time.monotonic() - started < 0.8
    if mode == "trickle":
        assert proxy.disconnected.wait(1)


def test_request_trickled_bound_address_is_not_an_idle_timeout(negotiation_proxy):
    proxy = negotiation_proxy("trickle")
    http = ChatDownloaderSession(proxy=proxy.url)
    http.session.trust_env = False
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout, match="completion deadline"):
            http.get("http://private.invalid/", timeout=Timeout(total=0.2, connect=0.5))
        assert time.monotonic() - started < 0.8
        assert proxy.disconnected.wait(1)
    finally:
        http.close()


def test_timed_generator_shutdown_unwinds_inflight_negotiation(negotiation_proxy):
    proxy = negotiation_proxy("trickle")
    finished = threading.Event()

    def source():
        try:
            with request_budget((30, 30)):
                _connection(proxy)._new_conn().close()
            yield "unexpected"
        finally:
            finished.set()

    timed = TimedGenerator(source())
    try:
        assert proxy.negotiating.wait(1)
        started = time.monotonic()
        timed.close()
        assert finished.wait(0.8)
        timed._worker.join(0.2)
        assert not timed._worker.is_alive()
        assert time.monotonic() - started < 1
        assert proxy.disconnected.wait(1)
    finally:
        timed.close()


@pytest.mark.parametrize("mode", ["reject", "eof", "stall"])
def test_negotiation_errors_preserve_context_without_proxy_secrets(
    negotiation_proxy, mode
):
    proxy = negotiation_proxy(mode)
    connection = _connection(proxy)
    connection.timeout = 0.1 if mode == "stall" else 0.5
    expected = ConnectTimeoutError if mode == "stall" else NewConnectionError
    with request_budget(2), pytest.raises(expected) as error:
        connection._new_conn()
    assert proxy.username == b"synthetic-user"
    assert proxy.password == b"synthetic-password"
    diagnostic_connection = (
        error.value.conn
        if isinstance(error.value, NewConnectionError)
        else error.value.args[0]
    )
    assert diagnostic_connection is connection
    rendered = str(error.value)
    assert "synthetic-user" not in rendered
    assert "synthetic-password" not in rendered
    assert "PROXY_REPLY_SECRET" not in rendered
    assert error.value.__suppress_context__


@pytest.mark.parametrize("version", [socks.SOCKS4, socks.SOCKS5])
@pytest.mark.parametrize("rdns", [False, True])
def test_protocol_dns_and_authentication_remain_usable(
    negotiation_proxy, version, rdns
):
    proxy = negotiation_proxy("auth" if version == socks.SOCKS5 else "success")
    host = "localhost" if rdns else "127.0.0.1"
    connection = _connection(proxy, version=version, rdns=rdns, host=host)
    with request_budget(2):
        sock = connection._new_conn()
        try:
            assert sock.gettimeout() == 0.5
            assert socket.socket.gettimeout(sock) == 0.5
            assert proxy.username == b"synthetic-user"
            if version == socks.SOCKS5:
                assert proxy.password == b"synthetic-password"
                assert proxy.destination[0] == (3 if rdns else 1)
            elif rdns:
                assert proxy.destination == b"localhost"
        finally:
            sock.close()
    assert proxy.disconnected.wait(1)


def test_ipv6_proxy_address_and_source_socket_options(negotiation_proxy):
    if not socket.has_ipv6:
        pytest.skip("IPv6 loopback unavailable")
    try:
        proxy = negotiation_proxy(ipv6=True)
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    proxy.host = f"[{proxy.host}]"
    connection = _connection(
        proxy,
        source_address=("::1", 0),
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    with request_budget(2):
        sock = connection._new_conn()
        try:
            assert sock.family == socket.AF_INET6
            assert sock.getsockname()[0] == "::1"
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        finally:
            sock.close()


@pytest.mark.parametrize("tls", [False, True])
def test_composed_connections_preserve_tls_hooks_and_http_requests(
    local_http, socks_proxy, tls
):
    original = SOCKSHTTPSConnection if tls else SOCKSConnection
    composed = type("ComposedSOCKSConnection", (SOCKSDeadlineMixin, original), {})
    assert issubclass(composed, original)
    target = local_http(tls=tls)
    http = ChatDownloaderSession(proxy=socks_proxy)
    http.session.trust_env = False
    kwargs = {"verify": str(TLS_FIXTURES / "certificate.pem")} if tls else {}
    try:
        assert http.get_json(target.url, **kwargs) == {"ok": True}
        manager = http.session.get_adapter(target.url).proxy_manager_for(socks_proxy)
        connection_class = manager.pool_classes_by_scheme[
            "https" if tls else "http"
        ].ConnectionCls
        assert issubclass(connection_class, SOCKSDeadlineMixin)
        assert http.get_json(target.url, **kwargs) == {"ok": True}
        assert target.requests[0][1] == target.requests[1][1]
    finally:
        http.close()


@pytest.mark.parametrize("phase", ["options", "bind", "negotiate"])
@pytest.mark.parametrize(
    "failure",
    [
        OSError,
        ValueError,
        socket.timeout,
        requests.exceptions.Timeout,
        _PollingCancelled,
    ],
)
def test_all_setup_failures_close_socket(monkeypatch, phase, failure):
    created = []

    class FailingSocket:
        def __init__(self, *_args):
            self.closed = False
            created.append(self)

        def setsockopt(self, *_args):
            if phase == "options":
                raise failure("PRIVATE_FAILURE")

        def settimeout(self, timeout):
            self.timeout = timeout

        def set_proxy(self, *_args):
            pass

        def bind(self, address):
            if phase == "bind":
                raise failure("PRIVATE_FAILURE")

        def connect_proxy(self, address, destination):
            raise failure("PRIVATE_FAILURE")

        def close(self):
            self.closed = True

    monkeypatch.setattr(http_socks, "_DeadlineSOCKSSocket", FailingSocket)
    proxy = SimpleNamespace(host="127.0.0.1", port=1080)
    connection = _connection(
        proxy,
        source_address=("127.0.0.1", 0),
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    expected = (
        _PollingCancelled
        if failure is _PollingCancelled
        else requests.exceptions.Timeout
        if failure is requests.exceptions.Timeout
        else ConnectTimeoutError
        if failure is socket.timeout
        else NewConnectionError
    )
    with request_budget(2), pytest.raises(expected):
        connection._new_conn()
    assert created
    assert all(sock.closed for sock in created)


@pytest.mark.parametrize("buffering", [0, -1])
def test_binary_makefile_retains_owner_and_restores_timeout(
    negotiation_proxy, buffering
):
    proxy = negotiation_proxy()
    with request_budget(2):
        sock = _connection(proxy)._new_conn()
        sock.settimeout(0.02)
        reader = sock.makefile("rb", buffering)
        try:
            if buffering == 0:
                assert isinstance(reader, DeadlineReader)
            assert sock._io_refs == 1
            with pytest.raises(socket.timeout):
                reader.read(1)
            assert sock.gettimeout() == 0.02
            assert socket.socket.gettimeout(sock) == 0.02
        finally:
            reader.close()
            assert sock._io_refs == 0
            sock.close()


def test_empty_proxy_address_list_is_content_free(monkeypatch):
    monkeypatch.setattr(http_socks.socket, "getaddrinfo", lambda *_args: [])
    proxy = SimpleNamespace(host="PRIVATE_PROXY", port=1080)
    with request_budget(2), pytest.raises(NewConnectionError) as error:
        _connection(proxy)._new_conn()
    assert "PRIVATE" not in str(error.value)


def test_socket_constructor_failure_tries_next_address(monkeypatch, negotiation_proxy):
    proxy = negotiation_proxy()
    addresses = socket.getaddrinfo(proxy.host, proxy.port, 0, socket.SOCK_STREAM)
    original = http_socks._DeadlineSOCKSSocket
    attempts = []

    class FailsOnce(original):
        def __init__(self, *args):
            attempts.append(args)
            if len(attempts) == 1:
                raise OSError("synthetic unavailable address family")
            super().__init__(*args)

    monkeypatch.setattr(http_socks, "_DeadlineSOCKSSocket", FailsOnce)
    monkeypatch.setattr(
        http_socks.socket, "getaddrinfo", lambda *_args: addresses + addresses
    )
    with request_budget(2):
        sock = _connection(proxy)._new_conn()
        sock.close()
    assert len(attempts) == 2


def test_reader_construction_failure_releases_socket_owner(
    monkeypatch, negotiation_proxy
):
    proxy = negotiation_proxy()
    with request_budget(2):
        sock = _connection(proxy)._new_conn()
        try:

            def cancelled(*_args, **_kwargs):
                raise _PollingCancelled

            monkeypatch.setattr(http_socks, "DeadlineReader", cancelled)
            with pytest.raises(_PollingCancelled):
                sock.makefile("rb", 0)
            assert sock._io_refs == 0
        finally:
            sock.close()


@pytest.mark.timeout(5)
def test_blocked_negotiation_write_obeys_idle_timeout_and_recovers():
    original, peer = socket.socketpair()
    sender = http_socks._DeadlineSOCKSSocket(
        original.family, original.type, original.proto, fileno=original.detach()
    )
    writer = None
    try:
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        sender.setblocking(False)
        payload = b"x" * 4096
        while True:
            try:
                socket.socket.send(sender, payload)
            except BlockingIOError:
                break
        sender.settimeout(0.25)
        writer = sender.makefile("wb", 0)
        with request_budget(2):
            started = time.monotonic()
            with pytest.raises(TimeoutError, match="SOCKS connection timed out"):
                writer.write(b"!")
            assert 0.2 <= time.monotonic() - started < 1.5
            assert sender.gettimeout() == 0.25
            assert socket.socket.gettimeout(sender) == 0.25
            # The short socket quanta must not poison the writer or its socket.
            peer.setblocking(False)
            while True:
                try:
                    peer.recv(65536)
                except BlockingIOError:
                    break
            assert writer.write(b"recovered") == len(b"recovered")
            peer.settimeout(0.5)
            assert peer.recv(65536) == b"recovered"
            assert sender.gettimeout() == 0.25
    finally:
        if writer is not None:
            writer.close()
        sender.close()
        original.close()
        peer.close()
    assert sender.fileno() == peer.fileno() == -1


@pytest.mark.timeout(5)
def test_refused_proxy_connect_preserves_classification_and_closes_fds(monkeypatch):
    created = []
    original_socket = http_socks._DeadlineSOCKSSocket

    def track_socket(*args, **kwargs):
        opened = original_socket(*args, **kwargs)
        created.append(opened)
        return opened

    monkeypatch.setattr(http_socks, "_DeadlineSOCKSSocket", track_socket)
    # Keep the endpoint reserved but not listening so refusal cannot race
    # another process claiming a temporarily released loopback port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        proxy = SimpleNamespace(host="127.0.0.1", port=reserved.getsockname()[1])
        connection = _connection(proxy)
        started = time.monotonic()
        with request_budget(2), pytest.raises(NewConnectionError) as error:
            connection._new_conn()
        assert error.value.conn is connection
        assert connection.sock is None
        assert time.monotonic() - started < 1.5
    assert created
    assert all(opened.fileno() == -1 for opened in created)
    assert all(opened.gettimeout() == 0.5 for opened in created)

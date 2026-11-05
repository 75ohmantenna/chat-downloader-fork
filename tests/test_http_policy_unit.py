# SPDX-License-Identifier: MIT

from __future__ import annotations

import gc
import gzip
import select
import socket
import ssl
import threading
import time
import zlib
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from types import SimpleNamespace
from urllib.parse import urlsplit
from weakref import ref

import pytest
import requests
from requests.adapters import BaseAdapter, HTTPAdapter
from urllib3 import PoolManager
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.util import Timeout

from chat_downloader.errors import ParsingError
from chat_downloader.sites import http_deadline, http_policy
from chat_downloader.sites.session import ChatDownloaderSession
from chat_downloader.sites.youtube.client_requests_continuation import (
    _get_continuation_info,
)
from chat_downloader.sites.youtube.client_requests_initial import _get_initial_info
from chat_downloader.utils.timed_generator import TimedGenerator

TLS_FIXTURES = Path(__file__).parent / "fixtures" / "http_policy"


def _relay(left, right, stop):
    peers = [left, right]
    try:
        while not stop.is_set():
            readable, _, _ = select.select(peers, [], [], 0.05)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                destination = right if source is left else left
                destination.sendall(data)
    except OSError:
        return


@pytest.fixture(name="local_http")
def local_http():
    with ExitStack() as stack:

        def start(*, tls=False):
            state = SimpleNamespace(
                body=b'{"ok": true}',
                encoding=None,
                mode="body",
                requests=[],
                stop=threading.Event(),
                accepted=threading.Event(),
                disconnected=threading.Event(),
                response_delay=0,
            )

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, *_args):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.do_GET()

                def do_CONNECT(self):
                    host, port = self.path.rsplit(":", 1)
                    with socket.create_connection(
                        (host, int(port)), timeout=1
                    ) as upstream:
                        self.send_response(200)
                        self.end_headers()
                        _relay(self.connection, upstream, state.stop)

                def do_GET(self):
                    state.requests.append(
                        (self.path, self.client_address[1], dict(self.headers))
                    )
                    state.accepted.set()
                    mode = state.mode
                    state.stop.wait(state.response_delay)
                    try:
                        if mode == "headers":
                            self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                        else:
                            redirect = mode == "redirect" and self.path != "/final"
                            self.send_response(302 if redirect else 200)
                            if redirect:
                                self.send_header("Location", "/final")
                            if state.encoding is not None:
                                self.send_header("Content-Encoding", state.encoding)
                            if mode == "chunk":
                                self.send_header("Transfer-Encoding", "chunked")
                            else:
                                length = (
                                    10**12
                                    if redirect
                                    or mode in {"trickle", "gzip-trickle", "stall"}
                                    else len(state.body)
                                )
                                self.send_header("Content-Length", str(length))
                            self.send_header("Set-Cookie", "sample=kept; Path=/")
                            self.end_headers()
                            if redirect:
                                return
                        if mode in {"headers", "chunk", "trickle", "gzip-trickle"}:
                            compressor = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
                            while not state.stop.is_set():
                                fragment = (
                                    compressor.compress(b"")
                                    + compressor.flush(zlib.Z_SYNC_FLUSH)
                                    if mode == "gzip-trickle"
                                    else b"x"
                                )
                                self.wfile.write(fragment)
                                self.wfile.flush()
                                state.stop.wait(0.02)
                        elif mode == "stall":
                            state.stop.wait(2)
                        else:
                            if mode == "slow":
                                state.stop.wait(0.2)
                            self.wfile.write(state.body)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        state.disconnected.set()
                        self.close_connection = True

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            if tls:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(
                    TLS_FIXTURES / "certificate.pem", TLS_FIXTURES / "private-key.pem"
                )
                server.socket = context.wrap_socket(server.socket, server_side=True)
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
            scheme = "https" if tls else "http"
            state.url = f"{scheme}://127.0.0.1:{server.server_port}"
            return state

        yield start


@pytest.fixture
def closed_responses(monkeypatch):
    closed = []
    original = requests.Response.close

    def close(response):
        try:
            original(response)
        finally:
            closed.append(response)

    monkeypatch.setattr(requests.Response, "close", close)
    return closed


def _http(**kwargs):
    return ChatDownloaderSession(proxy="", **kwargs)


@pytest.mark.parametrize("encoding", ["identity", "gzip", "x-gzip", "deflate", ""])
@pytest.mark.parametrize("overflow", [False, True])
def test_decoded_body_cap_precedes_text_json_and_releases_response(
    local_http, closed_responses, encoding, overflow
):
    server = local_http()
    decoded = b"x" * (http_policy.MAX_RESPONSE_BYTES + int(overflow))
    server.encoding = encoding
    server.body = (
        gzip.compress(decoded)
        if encoding in {"gzip", "x-gzip"}
        else zlib.compress(decoded)
        if encoding == "deflate"
        else decoded
    )
    http = _http()
    try:
        if overflow:
            with pytest.raises(
                ParsingError, match="decoded response exceeds the 16 MiB"
            ):
                http.get(server.url)
        else:
            response = http.get(server.url)
            assert response.content == decoded
            assert response.text == decoded.decode()
        assert closed_responses[-1].raw.closed
        assert http.get_cookie("sample") == "kept"
        server.encoding, server.body = None, b'{"ok": true}'
        assert http.get_json(server.url) == {"ok": True}
    finally:
        http.close()


@pytest.mark.parametrize("mode", ["trickle", "headers", "chunk", "gzip-trickle"])
def test_absolute_deadline_stops_progressing_wire_reads(local_http, mode):
    server = local_http()
    server.mode = mode
    server.encoding = "gzip" if mode == "gzip-trickle" else None
    http = _http(connect_timeout=0.15, read_timeout=0.25)
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.RequestException):
            http.get(server.url)
        assert time.monotonic() - started < 1.5
        assert server.accepted.is_set()
    finally:
        http.close()


@pytest.mark.parametrize("mode", ["trickle", "headers", "chunk", "gzip-trickle"])
def test_timed_stop_unwinds_body_worker_and_closes_owned_response(
    local_http, closed_responses, mode
):
    server = local_http()
    server.mode = mode
    server.encoding = "gzip" if mode == "gzip-trickle" else None
    http = _http(connect_timeout=2, read_timeout=2)
    finalized = threading.Event()

    def messages():
        try:
            http.get(server.url)
            yield {"unexpected": True}
        finally:
            finalized.set()

    timed = TimedGenerator(messages(), timeout=0.2)
    try:
        assert list(timed) == []
        assert finalized.wait(1)
        timed._worker.join(1)
        assert not timed._worker.is_alive()
        assert server.disconnected.wait(1)
        if mode != "headers":
            assert closed_responses[-1].raw.closed
    finally:
        timed.close()
        http.close()


@pytest.mark.parametrize("tls", [False, True])
def test_short_socket_polls_preserve_read_timeout_cookies_and_keepalive(
    local_http, tls
):
    server = local_http(tls=tls)
    http = _http(connect_timeout=1, read_timeout=0.8)
    kwargs = {"verify": str(TLS_FIXTURES / "certificate.pem")} if tls else {}
    try:
        assert http.get_json(server.url, **kwargs) == {"ok": True}
        manager = http.session.get_adapter(server.url).poolmanager
        pool = next(iter(manager.pools._container.values()))
        connection = next(item for item in pool.pool.queue if item is not None)
        assert connection.sock.gettimeout() > 0.5
        http.update_headers({"Accept-Encoding": "br"})
        http.replace_headers({}, {"Accept-Encoding"})
        assert http.apply_request_profile("youtube_web")
        server.mode = "slow"
        assert http.post(server.url, json={"request": True}, **kwargs).json() == {
            "ok": True
        }
        assert server.requests[0][1] == server.requests[1][1]
        assert server.requests[1][2]["Cookie"] == "sample=kept"
        assert server.requests[0][2]["Accept-Encoding"] == "gzip, deflate"
        assert server.requests[1][2]["Accept-Encoding"] == "gzip, deflate"
    finally:
        http.close()


def test_idle_read_timeout_is_not_extended_by_polling(local_http, closed_responses):
    server = local_http()
    server.mode = "stall"
    http = _http(connect_timeout=1, read_timeout=0.15)
    started = time.monotonic()
    try:
        with pytest.raises(
            requests.exceptions.ConnectionError, match="Unable to read provider"
        ):
            http.get(server.url)
        assert time.monotonic() - started < 0.8
        assert closed_responses[-1].raw.closed
    finally:
        http.close()


@pytest.mark.parametrize("method", ["get", "post"])
def test_redirect_rejection_never_consumes_body(local_http, closed_responses, method):
    server = local_http()
    server.mode = "redirect"
    http = _http(connect_timeout=1, read_timeout=1)
    try:
        with pytest.raises(ParsingError, match="Unexpected HTTP redirect"):
            getattr(http, method)(server.url)
        assert len(server.requests) == 1
        assert closed_responses[-1].raw.closed
        assert closed_responses[-1].content == b""
    finally:
        http.close()


def test_tls_same_origin_redirect_closes_intermediate_response(
    local_http, closed_responses
):
    server = local_http(tls=True)
    server.mode = "redirect"
    http = _http()
    try:
        result = http.get_json(
            server.url,
            allow_redirects=True,
            verify=str(TLS_FIXTURES / "certificate.pem"),
            params={"once": "yes"},
        )
        assert result == {"ok": True}
        assert [urlsplit(path).path for path, _, _ in server.requests] == [
            "/",
            "/final",
        ]
        assert "once=yes" in server.requests[0][0]
        assert "once=yes" not in server.requests[1][0]
        assert server.requests[1][2]["Cookie"] == "sample=kept"
        assert all(response.raw.closed for response in closed_responses)
    finally:
        http.close()


@pytest.mark.parametrize("encoding", ["br", "gzip, deflate", "unknown"])
def test_unsupported_coding_fails_before_decoding_and_cannot_be_bypassed_by_hooks(
    local_http, closed_responses, encoding
):
    server = local_http()
    server.encoding = encoding
    server.body = b"PRIVATE_BODY_SENTINEL"
    calls = []
    http = _http(headers={"Accept-Encoding": encoding})
    try:
        with pytest.raises(
            ParsingError, match="Unsupported provider HTTP content encoding"
        ) as error:
            http.get(
                server.url,
                hooks={"response": lambda response, **_: calls.append(response)},
            )
        assert "PRIVATE_BODY_SENTINEL" not in str(error.value)
        assert calls == []
        assert closed_responses[-1].raw.closed
    finally:
        http.close()


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("proxy_tls", [False, True])
def test_http_proxy_and_connect_tunnel_keep_bounded_response_policy(
    local_http, tls, proxy_tls
):
    target = local_http(tls=tls)
    proxy = local_http(tls=proxy_tls)
    http = ChatDownloaderSession(proxy=proxy.url, connect_timeout=1, read_timeout=1)
    http.session.trust_env = False
    kwargs = (
        {"verify": str(TLS_FIXTURES / "certificate.pem")} if tls or proxy_tls else {}
    )
    try:
        assert http.get_json(target.url, **kwargs) == {"ok": True}
        server = target if tls else proxy
        server.mode = "trickle"
        with pytest.raises(requests.exceptions.RequestException):
            http.get(target.url, timeout=(0.1, 0.2), **kwargs)
    finally:
        http.close()


@pytest.mark.parametrize(
    "timeout",
    [0.5, (0.2, 0.3), Timeout(connect=0.2, read=0.3), Timeout(total=0.5), None],
)
def test_explicit_timeout_forms_still_consume_bounded_responses(local_http, timeout):
    server = local_http()
    http = _http()
    try:
        assert http.get_json(server.url, timeout=timeout) == {"ok": True}
    finally:
        http.close()


@pytest.mark.parametrize("stream", [False, True])
def test_exposed_requests_session_automatically_owns_its_budget(
    local_http, closed_responses, stream
):
    server = local_http()
    http = _http()
    observed = []
    try:
        response = http.session.get(
            server.url,
            timeout=(0.2, 0.5),
            stream=stream,
            hooks={"response": lambda response, **_: observed.append(response.json())},
        )
        assert observed == [{"ok": True}]
        assert b"".join(response.iter_content(2)) == server.body
        assert closed_responses[-1].raw.closed
        server.mode = "trickle"
        with pytest.raises(requests.exceptions.Timeout, match="completion deadline"):
            http.session.get(server.url, timeout=(0.1, 0.2), stream=stream)
        assert closed_responses[-1].raw.closed
    finally:
        http.close()


@pytest.mark.parametrize("consumer", ["initial-page", "continuation"])
def test_youtube_consumers_cannot_parse_before_decoded_body_limit(
    local_http, monkeypatch, consumer
):
    server = local_http(tls=consumer == "initial-page")
    server.encoding = "gzip"
    server.body = gzip.compress(b"PRIVATE_BODY_SENTINEL" * 100)
    monkeypatch.setattr(http_policy, "MAX_RESPONSE_BYTES", 128)
    http = _http()
    kwargs = {"verify": str(TLS_FIXTURES / "certificate.pem")}
    try:
        with pytest.raises(ParsingError, match="decoded response exceeds") as error:
            if consumer == "initial-page":
                _get_initial_info(
                    server.url,
                    lambda url, **options: http.get(url, **kwargs, **options),
                    None,
                    "data",
                    "cfg",
                    "player",
                )
            else:
                _get_continuation_info(server.url, http.post, {"max_attempts": 1})
        assert "PRIVATE_BODY_SENTINEL" not in str(error.value)
        assert len(server.requests) == 1
    finally:
        http.close()


def test_malformed_compression_is_content_free_and_releases_response(
    local_http, closed_responses
):
    server = local_http()
    server.encoding = "gzip"
    server.body = b"PRIVATE_MALFORMED_COMPRESSION"
    http = _http()
    try:
        with pytest.raises(requests.exceptions.ConnectionError) as error:
            http.get(server.url)
        assert str(error.value) == "Unable to read provider HTTP response."
        assert closed_responses[-1].raw.closed
    finally:
        http.close()


@pytest.mark.parametrize("timeout", [None, 0.02])
def test_raw_socket_reader_restores_timeout_after_success_and_failure(timeout):
    client, peer = socket.socketpair()
    client.settimeout(timeout)
    try:
        with http_deadline.request_budget((0.2, 0.3)):
            reader = http_deadline.DeadlineReader(client)
            try:
                peer.sendall(b"x")
                result = bytearray(1)
                assert reader.readinto(result) == 1
                assert result == b"x"
                assert client.gettimeout() == timeout
                if timeout is not None:
                    with pytest.raises(TimeoutError, match="HTTP read timed out"):
                        reader.readinto(result)
                    assert client.gettimeout() == timeout
            finally:
                reader.close()
    finally:
        client.close()
        peer.close()


@pytest.fixture(name="socks_proxy")
def socks_proxy():
    stop = threading.Event()

    class Handler(StreamRequestHandler):
        def handle(self):
            version, methods = self.rfile.read(2)
            assert version == 5
            self.rfile.read(methods)
            self.wfile.write(b"\x05\x00")
            version, command, _, address_type = self.rfile.read(4)
            assert (version, command) == (5, 1)
            if address_type == 1:
                host = socket.inet_ntoa(self.rfile.read(4))
            else:
                host = self.rfile.read(self.rfile.read(1)[0]).decode()
            port = int.from_bytes(self.rfile.read(2), "big")
            with socket.create_connection((host, port), timeout=1) as upstream:
                self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                _relay(self.connection, upstream, stop)

    with ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        try:
            yield f"socks5h://127.0.0.1:{server.server_address[1]}"
        finally:
            stop.set()
            server.shutdown()
            thread.join(2)


@pytest.mark.parametrize("tls", [False, True])
def test_socks_proxy_uses_stable_bounded_connection_classes(
    local_http, socks_proxy, tls
):
    server = local_http(tls=tls)
    http = ChatDownloaderSession(proxy=socks_proxy)
    http.session.trust_env = False
    kwargs = {"verify": str(TLS_FIXTURES / "certificate.pem")} if tls else {}
    adapter = http.session.get_adapter(server.url)
    try:
        assert http.get_json(server.url, **kwargs) == {"ok": True}
        manager = adapter.proxy_manager_for(socks_proxy)
        classes = manager.pool_classes_by_scheme
        assert PoolManager().pool_classes_by_scheme["http"] is HTTPConnectionPool
        assert http.get_json(server.url, **kwargs) == {"ok": True}
        assert manager.pool_classes_by_scheme is classes
        assert server.requests[0][1] == server.requests[1][1]
        server.mode = "trickle"
        with pytest.raises(requests.exceptions.RequestException):
            http.get(server.url, timeout=(0.1, 0.2), **kwargs)
    finally:
        http.close()


def test_initial_page_redirects_share_one_absolute_deadline(local_http):
    server = local_http(tls=True)
    server.mode = "redirect"
    server.response_delay = 0.35
    http = _http(connect_timeout=0.1, read_timeout=0.5)
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.RequestException):
            http.get(
                server.url,
                allow_redirects=True,
                verify=str(TLS_FIXTURES / "certificate.pem"),
            )
        assert time.monotonic() - started < 1.2
        assert len(server.requests) == 2
    finally:
        http.close()


def test_tls_verification_failure_does_not_poison_reusable_session(local_http):
    server = local_http(tls=True)
    http = _http()
    try:
        with pytest.raises(requests.exceptions.SSLError):
            http.get(server.url)
        assert server.requests == []
        assert http.get_json(
            server.url, verify=str(TLS_FIXTURES / "certificate.pem")
        ) == {"ok": True}
    finally:
        http.close()


def test_exposed_session_redirect_chain_keeps_one_completion_budget(local_http):
    server = local_http()
    server.mode = "redirect"
    server.response_delay = 0.35
    http = _http()
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.RequestException):
            http.session.get(server.url, timeout=(0.1, 0.5))
        assert len(server.requests) == 2
        assert time.monotonic() - started < 1.2
    finally:
        http.close()


def test_host_specific_adapter_retains_tls_context_and_replaces_unbounded_pools(
    local_http, monkeypatch
):
    server = local_http(tls=True)
    context = ssl.create_default_context(cafile=str(TLS_FIXTURES / "certificate.pem"))

    class TLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = context
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.trust_env = False
    adapter = TLSAdapter()
    session.mount(server.url + "/", adapter)
    assert session.get(server.url).json() == {"ok": True}
    http = ChatDownloaderSession(session_factory=lambda: session, proxy="")
    try:
        assert http.get_json(server.url) == {"ok": True}
        assert adapter.poolmanager.connection_pool_kw["ssl_context"] is context
        assert server.requests[0][1] != server.requests[1][1]
        monkeypatch.setattr(http_policy, "MAX_RESPONSE_BYTES", 128)
        server.body = b"x" * 129
        with pytest.raises(ParsingError, match="decoded response exceeds"):
            http.get(server.url)
    finally:
        http.close()


@pytest.mark.parametrize(
    "method", ["send", "build_response", "get_connection_with_tls_context"]
)
def test_unsupported_custom_adapter_fails_closed_before_network(local_http, method):
    server = local_http()
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("custom transport must not run")

    custom = type("UnsupportedAdapter", (HTTPAdapter,), {method: forbidden})()
    session = requests.Session()
    session.trust_env = False
    session.mount(server.url + "/", custom)
    with pytest.raises(ParsingError, match="Unsupported custom HTTP adapter"):
        ChatDownloaderSession(session_factory=lambda: session, proxy="")
    assert called == []
    assert server.requests == []


def test_new_unsupported_adapter_mount_is_rejected_before_request(local_http):
    server = local_http()

    class UnsupportedAdapter(BaseAdapter):
        def close(self):
            pass

        def send(self, *_args, **_kwargs):
            raise AssertionError("unsupported adapter must not run")

    http = _http()
    http.session.mount(server.url + "/", UnsupportedAdapter())
    try:
        with pytest.raises(ParsingError, match="Unsupported custom HTTP adapter"):
            http.session.get(server.url)
        assert server.requests == []
    finally:
        http.close()


def test_custom_session_sender_is_rejected_before_request(local_http):
    server = local_http()

    class UnsupportedSession(requests.Session):
        def send(self, *_args, **_kwargs):
            raise AssertionError("custom session sender must not run")

    with pytest.raises(ParsingError, match="Unsupported custom HTTP session sender"):
        ChatDownloaderSession(session_factory=UnsupportedSession, proxy="")
    assert server.requests == []


def test_custom_pool_manager_is_rejected_without_discarding_its_policy(local_http):
    server = local_http()

    class CustomManager(PoolManager):
        pass

    session = requests.Session()
    adapter = HTTPAdapter()
    adapter.poolmanager = CustomManager()
    session.mount(server.url + "/", adapter)
    with pytest.raises(ParsingError, match="Unsupported custom HTTP pool manager"):
        ChatDownloaderSession(session_factory=lambda: session, proxy="")
    assert server.requests == []


def test_custom_response_connection_is_rejected_before_its_header_reader(local_http):
    from urllib3.connection import HTTPConnection

    server = local_http()

    class CustomConnection(HTTPConnection):
        def getresponse(self):
            raise AssertionError("custom header reader must not run")

    class CustomPool(HTTPConnectionPool):
        ConnectionCls = CustomConnection

    session = requests.Session()
    adapter = HTTPAdapter()
    adapter.poolmanager.pool_classes_by_scheme = {"http": CustomPool}
    session.mount(server.url + "/", adapter)
    with pytest.raises(ParsingError, match="Unsupported custom HTTP connection"):
        ChatDownloaderSession(session_factory=lambda: session, proxy="")
    assert server.requests == []


def test_deflate_empty_blocks_cannot_retain_unbounded_fallback_history(
    local_http, closed_responses, monkeypatch
):
    server = local_http()
    server.encoding = "deflate"
    # Valid zlib header followed by non-final, empty stored blocks. urllib3
    # otherwise retains every byte while deciding whether raw-deflate fallback
    # is required, and read1 does not return until decoded output or EOF.
    server.body = b"\x78\x9c" + b"\x00\x00\x00\xff\xff" * 40000
    monkeypatch.setattr(http_deadline, "MAX_WIRE_BYTES", 64 * 1024)
    http = _http()
    try:
        with pytest.raises(ParsingError, match="32 MiB wire limit"):
            http.get(server.url)
        response = closed_responses[-1]
        assert response.raw.closed
        retained = getattr(response.raw._decoder, "_first_try_data", b"")
        assert len(retained) <= http_deadline.MAX_WIRE_BYTES
    finally:
        http.close()


@pytest.mark.parametrize("overflow", [False, True])
def test_wire_byte_quota_accepts_exact_boundary_and_closes_owner(monkeypatch, overflow):
    monkeypatch.setattr(http_deadline, "MAX_WIRE_BYTES", 4)
    client, peer = socket.socketpair()
    client.settimeout(0.5)
    try:
        with http_deadline.request_budget((1, 1)):
            reader = http_deadline.DeadlineReader(client)
            try:
                peer.sendall(b"12345" if overflow else b"1234")
                peer.shutdown(socket.SHUT_WR)
                assert reader.readinto(bytearray(4)) == 4
                if overflow:
                    with pytest.raises(ParsingError, match="32 MiB wire limit"):
                        reader.readinto(bytearray(1))
                else:
                    assert reader.readinto(bytearray(1)) == 0
                assert client.gettimeout() == 0.5
            finally:
                reader.close()
            assert reader.closed
    finally:
        client.close()
        peer.close()


def test_adapter_type_refusing_policy_is_content_free_and_never_requested(local_http):
    server = local_http()

    class RefusePolicy(type):
        def __new__(cls, name, bases, namespace):
            if name.startswith("Bounded"):
                raise TypeError("PRIVATE_ADAPTER_DETAIL")
            return super().__new__(cls, name, bases, namespace)

    class CustomAdapter(HTTPAdapter, metaclass=RefusePolicy):
        pass

    session = requests.Session()
    session.mount(server.url + "/", CustomAdapter())
    with pytest.raises(
        ParsingError, match="Unable to enforce bounded requests"
    ) as error:
        ChatDownloaderSession(session_factory=lambda: session, proxy="")
    assert "PRIVATE_ADAPTER_DETAIL" not in str(error.value)
    assert server.requests == []


def test_short_lived_custom_adapter_and_pool_classes_are_not_globally_retained():
    def configure():
        class CustomAdapter(HTTPAdapter):
            pass

        class CustomPool(HTTPConnectionPool):
            pass

        session = requests.Session()
        adapter = CustomAdapter()
        adapter.poolmanager.pool_classes_by_scheme = {"http": CustomPool}
        session.mount("http://owned.invalid/", adapter)
        http = ChatDownloaderSession(session_factory=lambda: session, proxy="")
        references = [
            ref(CustomAdapter),
            ref(CustomPool),
            ref(type(adapter)),
            ref(adapter.poolmanager.pool_classes_by_scheme["http"]),
        ]
        http.close()
        return references

    references = [item for _ in range(50) for item in configure()]
    gc.collect()
    assert all(reference() is None for reference in references)


def test_explicit_pool_acquisition_timeout_does_not_consume_a_capacity_token():
    from urllib3.exceptions import EmptyPoolError

    from chat_downloader.sites.http_waits import DeadlinePoolMixin

    pool = DeadlinePoolMixin("127.0.0.1", maxsize=1, block=True)
    try:
        with http_deadline.request_budget((1, 1)):
            owner = pool._get_conn()
            try:
                with pytest.raises(EmptyPoolError, match="pool acquisition timed out"):
                    pool._get_conn(timeout=0.01)
            finally:
                pool._put_conn(owner)
            replacement = pool._get_conn(timeout=0.01)
            pool._put_conn(replacement)
    finally:
        pool.close()

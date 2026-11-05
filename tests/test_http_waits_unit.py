# SPDX-License-Identifier: MIT

from __future__ import annotations

import select
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.util import Retry, Timeout

from chat_downloader.errors import ParsingError
from chat_downloader.sites import http_policy
from chat_downloader.sites.session import ChatDownloaderSession
from chat_downloader.utils.timed_generator import TimedGenerator

pytestmark = pytest.mark.timeout(8)


@pytest.fixture
def wait_http():
    state = SimpleNamespace(
        stop=threading.Event(),
        owner_entered=threading.Event(),
        release_owner=threading.Event(),
        retry_waiting=threading.Event(),
        extra_retry=threading.Event(),
        discarded=threading.Event(),
        probe_entered=threading.Event(),
        failures=1,
        retry_after=None,
        failure_body="empty",
        wait_after=1,
        attempts=0,
        requests=[],
        sockets=[],
        handlers=[],
        workers=[],
        timed=[],
        lock=threading.Lock(),
    )
    body = b'{"ok": true}'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            with state.lock:
                state.sockets.append(self.connection)
                state.handlers.append(threading.current_thread())

        def handle(self):
            with suppress(OSError):
                super().handle()

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.do_GET()

        def do_GET(self):
            with state.lock:
                state.requests.append(
                    SimpleNamespace(
                        path=self.path,
                        method=self.command,
                        port=self.client_address[1],
                        at=time.monotonic(),
                    )
                )
                if self.path == "/retry":
                    state.attempts += 1
                attempt = state.attempts
            failed = self.path == "/retry" and attempt <= state.failures
            self.send_response(503 if failed else 200)
            if failed and state.retry_after is not None:
                self.send_header("Retry-After", state.retry_after)
            ignored_body = failed and state.failure_body != "empty"
            self.send_header(
                "Content-Length",
                str(10**12 if ignored_body else 0 if failed else len(body)),
            )
            self.end_headers()
            self.wfile.flush()
            if self.path == "/probe":
                state.probe_entered.set()
            if failed:
                if attempt == state.wait_after:
                    state.retry_waiting.set()
                elif attempt > state.wait_after:
                    state.extra_retry.set()
                if ignored_body:
                    self.discarded_body()
                return
            if self.path == "/owner":
                state.owner_entered.set()
                if not state.release_owner.wait(6):
                    return
            with suppress(OSError):
                self.wfile.write(body)
                self.wfile.flush()

        def discarded_body(self):
            self.close_connection = True
            try:
                while not state.stop.is_set():
                    if state.failure_body == "trickle":
                        self.wfile.write(b"PRIVATE_IGNORED_BODY" * 16)
                        self.wfile.flush()
                    readable, _, _ = select.select([self.connection], [], [], 0.02)
                    if readable and not self.connection.recv(1):
                        state.discarded.set()
                        return
            except OSError:
                state.discarded.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    state.url = f"http://127.0.0.1:{server.server_port}"
    serving.start()
    try:
        yield state
    finally:
        state.stop.set()
        state.release_owner.set()
        for timed in state.timed:
            timed.close()
        server.shutdown()
        server.server_close()
        with state.lock:
            sockets = list(state.sockets)
        for connection in sockets:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        serving.join(1)
        assert not serving.is_alive()
        for worker in [*state.workers, *state.handlers]:
            worker.join(2)
            assert not worker.is_alive()


def _factory_http(server, retries, **pool_options):
    session = requests.Session()
    session.trust_env = False
    adapter = HTTPAdapter(max_retries=retries, **pool_options)
    session.mount(server.url + "/", adapter)
    http = ChatDownloaderSession(session_factory=lambda: session, proxy="")
    return http, adapter


def _request_worker(server, operation):
    result = SimpleNamespace(
        started=threading.Event(),
        finished=threading.Event(),
        values=[],
        errors=[],
    )

    def run():
        result.started.set()
        try:
            result.values.append(operation())
        except BaseException as error:
            result.errors.append(error)
        finally:
            result.finished.set()

    result.thread = threading.Thread(target=run, daemon=True)
    server.workers.append(result.thread)
    result.thread.start()
    return result


def _timed_request(server, operation):
    result = SimpleNamespace(finished=threading.Event(), values=[], errors=[])

    def messages():
        try:
            value = operation()
            result.values.append(value)
            yield value
        except Exception as error:
            result.errors.append(error)
            raise
        finally:
            result.finished.set()

    result.timed = TimedGenerator(messages())
    server.timed.append(result.timed)
    server.workers.append(result.timed._worker)
    return result


def _finished(worker):
    assert worker.finished.wait(1.5)
    worker.thread.join(0.2)
    assert not worker.thread.is_alive()


def _retry_wait(server, kind):
    if kind == "backoff":
        server.failures = server.wait_after = 2
        return Retry(total=3, status_forcelist={503}, backoff_factor=1.5)
    server.retry_after = "3"
    return 3 if kind == "retry-after-int" else Retry(total=3)


@pytest.mark.parametrize("kind", ["retry-after", "retry-after-int", "backoff"])
@pytest.mark.parametrize("exposed", [False, True])
def test_factory_retry_sleep_consumes_one_absolute_request_budget(
    wait_http, kind, exposed
):
    retries = _retry_wait(wait_http, kind)
    http, _adapter = _factory_http(wait_http, retries)
    request = http.session.get if exposed else http.get
    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout, match="completion deadline"):
            request(wait_http.url + "/retry", timeout=Timeout(total=0.35, read=2))
        assert time.monotonic() - started < 1.5
        assert wait_http.retry_waiting.is_set()
        assert wait_http.attempts == wait_http.wait_after
        assert http.get_json(wait_http.url + "/ok", timeout=1) == {"ok": True}
    finally:
        http.close()


@pytest.mark.parametrize("kind", ["retry-after", "retry-after-int", "backoff"])
def test_timed_generator_cancellation_unwinds_factory_retry_sleep(wait_http, kind):
    retries = _retry_wait(wait_http, kind)
    http, _adapter = _factory_http(wait_http, retries)
    worker = _timed_request(
        wait_http,
        lambda: http.get_json(
            wait_http.url + "/retry", timeout=Timeout(total=15, read=2)
        ),
    )
    try:
        assert wait_http.retry_waiting.wait(2)
        assert not wait_http.extra_retry.wait(0.15)
        assert not worker.finished.is_set()
        worker.timed.close()
        assert worker.finished.wait(1)
        worker.timed._worker.join(0.2)
        assert not worker.timed._worker.is_alive()
        assert worker.values == worker.errors == []
        assert list(worker.timed) == []
        assert wait_http.attempts == wait_http.wait_after
        assert http.get_json(wait_http.url + "/ok", timeout=1) == {"ok": True}
    finally:
        worker.timed.close()
        http.close()


@pytest.mark.parametrize("method", ["get", "post"])
def test_factory_retry_preserves_configuration_and_allowed_methods(wait_http, method):
    retries = Retry(
        total=4,
        connect=2,
        read=3,
        redirect=1,
        status=3,
        other=1,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=frozenset({503}),
        backoff_factor=0.02,
        backoff_max=0.5,
        backoff_jitter=0,
        raise_on_redirect=False,
        raise_on_status=False,
        respect_retry_after_header=False,
        retry_after_max=7,
        remove_headers_on_redirect=frozenset({"Authorization", "Cookie"}),
    )
    expected = vars(retries).copy()
    wait_http.failures = 2
    wait_http.retry_after = "3"
    http, adapter = _factory_http(
        wait_http,
        retries,
        pool_connections=2,
        pool_maxsize=1,
        pool_block=True,
    )
    try:
        for name, value in expected.items():
            assert getattr(adapter.max_retries, name) == value
        settings = adapter.poolmanager.connection_pool_kw
        assert settings["maxsize"] == 1
        assert settings["block"] is True
        response = getattr(http, method)(wait_http.url + "/retry", timeout=1)
        assert response.status_code == (200 if method == "get" else 503)
        assert response.content == (b'{"ok": true}' if method == "get" else b"")
        assert wait_http.attempts == (3 if method == "get" else 1)
        for name, value in expected.items():
            assert getattr(adapter.max_retries, name) == value
        assert vars(retries) == expected
    finally:
        http.close()


@pytest.mark.parametrize("retries", [2, Retry(total=2)])
def test_factory_integer_and_retry_object_keep_healthy_retry_after_behavior(
    wait_http, retries
):
    wait_http.retry_after = "1"
    http, adapter = _factory_http(wait_http, retries)
    started = time.monotonic()
    try:
        assert http.get_json(
            wait_http.url + "/retry", timeout=Timeout(total=3, read=2)
        ) == {
            "ok": True,
        }
        assert wait_http.attempts == 2
        assert wait_http.requests[1].at - wait_http.requests[0].at >= 0.9
        assert time.monotonic() - started < 2.5
        assert adapter.max_retries.total == 2
    finally:
        http.close()


@pytest.mark.parametrize("raise_on_status", [False, True])
def test_factory_retry_preserves_exhausted_status_policy(wait_http, raise_on_status):
    wait_http.failures = 10
    retries = Retry(total=1, status_forcelist={503}, raise_on_status=raise_on_status)
    http, _adapter = _factory_http(wait_http, retries)
    try:
        if raise_on_status:
            with pytest.raises(requests.exceptions.RetryError):
                http.get(wait_http.url + "/retry", timeout=1)
        else:
            assert http.get(wait_http.url + "/retry", timeout=1).status_code == 503
        assert wait_http.attempts == 2
        assert http.get_json(wait_http.url + "/ok", timeout=1) == {"ok": True}
    finally:
        http.close()


@pytest.mark.parametrize("failure_body", ["huge", "trickle"])
def test_retry_discards_unbounded_intermediate_body_before_reusing_pool(
    wait_http,
    monkeypatch,
    failure_body,
):
    wait_http.failure_body = failure_body
    monkeypatch.setattr(http_policy, "MAX_RESPONSE_BYTES", 64)
    http, _adapter = _factory_http(
        wait_http,
        Retry(total=1, status_forcelist={503}),
        pool_maxsize=1,
        pool_block=True,
    )
    started = time.monotonic()
    try:
        assert http.get_json(
            wait_http.url + "/retry",
            timeout=Timeout(total=0.8, read=2),
        ) == {"ok": True}
        assert time.monotonic() - started < 1.5
        assert wait_http.attempts == 2
        assert wait_http.discarded.wait(1)
        first, retried = wait_http.requests
        assert first.port != retried.port
        assert http.get_json(wait_http.url + "/ok", timeout=1) == {"ok": True}
        assert wait_http.requests[-1].port == retried.port
    finally:
        http.close()


@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_blocking_pool_waiter_leaves_owner_and_single_connection_capacity_intact(
    wait_http,
    stop,
):
    http, adapter = _factory_http(wait_http, 0, pool_maxsize=1, pool_block=True)
    long_timeout = Timeout(total=5, connect=1, read=4)
    owner = _request_worker(
        wait_http,
        lambda: http.get_json(wait_http.url + "/owner", timeout=long_timeout),
    )
    cancelled = None
    try:
        assert wait_http.owner_entered.wait(2)
        assert not owner.finished.is_set()
        if stop == "deadline":
            waiter = _request_worker(
                wait_http,
                lambda: http.get_json(
                    wait_http.url + "/waiter",
                    timeout=Timeout(total=0.3, read=2),
                ),
            )
            _finished(waiter)
            assert waiter.values == []
            assert len(waiter.errors) == 1
            assert isinstance(waiter.errors[0], requests.exceptions.Timeout)
            assert "completion deadline" in str(waiter.errors[0])
        else:
            entered = threading.Event()

            def request_waiter():
                entered.set()
                return http.get_json(wait_http.url + "/waiter", timeout=long_timeout)

            cancelled = _timed_request(wait_http, request_waiter)
            assert entered.wait(1)
            assert not cancelled.finished.wait(0.15)
            cancelled.timed.close()
            assert cancelled.finished.wait(1)
            cancelled.timed._worker.join(0.2)
            assert not cancelled.timed._worker.is_alive()
            assert cancelled.values == cancelled.errors == []
        assert [request.path for request in wait_http.requests] == ["/owner"]
        assert not owner.finished.is_set()
        probe = _request_worker(
            wait_http,
            lambda: http.get_json(wait_http.url + "/probe", timeout=long_timeout),
        )
        assert probe.started.wait(1)
        # A fictitious returned pool token would let this request open a second
        # socket while the real connection is still exclusively owned.
        assert not wait_http.probe_entered.wait(0.2)
        assert not probe.finished.is_set()
        wait_http.release_owner.set()
        _finished(owner)
        _finished(probe)
        assert owner.errors == probe.errors == []
        assert owner.values == probe.values == [{"ok": True}]
        assert http.get_json(wait_http.url + "/ok", timeout=1) == {"ok": True}
        assert [request.path for request in wait_http.requests] == [
            "/owner",
            "/probe",
            "/ok",
        ]
        assert len({request.port for request in wait_http.requests}) == 1
        assert adapter.poolmanager.connection_pool_kw["maxsize"] == 1
        assert adapter.poolmanager.connection_pool_kw["block"] is True
    finally:
        wait_http.release_owner.set()
        if cancelled is not None:
            cancelled.timed.close()
        owner.thread.join(1)
        http.close()


@pytest.mark.parametrize(
    "custom", ["retry", "retry-sleep", "pool-acquire", "pool-urlopen"]
)
def test_unknown_retry_or_pool_wait_implementation_fails_closed(wait_http, custom):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("unsupported wait implementation must not run")

    retries = 1
    if custom.startswith("retry"):
        methods = {"sleep": forbidden} if custom == "retry-sleep" else {}
        retries = type("UnknownRetry", (Retry,), methods)(total=1)
    adapter = HTTPAdapter(max_retries=retries)
    if custom.startswith("pool"):
        method = "_get_conn" if custom == "pool-acquire" else "urlopen"
        pool = type("UnknownPool", (HTTPConnectionPool,), {method: forbidden})
        adapter.poolmanager.pool_classes_by_scheme = {
            **adapter.poolmanager.pool_classes_by_scheme,
            "http": pool,
        }
    session = requests.Session()
    session.trust_env = False
    session.mount(wait_http.url + "/", adapter)
    http = None
    try:
        with pytest.raises(ParsingError) as error:
            http = ChatDownloaderSession(session_factory=lambda: session, proxy="")
            http.get(wait_http.url + "/ok", timeout=1)
        assert "unsupported wait implementation" not in str(error.value)
        assert calls == []
        assert wait_http.requests == []
    finally:
        if http is not None:
            http.close()
        else:
            session.close()

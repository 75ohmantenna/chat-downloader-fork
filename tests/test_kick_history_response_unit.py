# SPDX-License-Identifier: MIT
from __future__ import annotations

import gzip
import hashlib
import io
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import cloudscraper  # type: ignore[import-untyped]
import pytest
import requests
from curl_cffi.requests import Session as CurlSession

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import (
    api_client,
    history_response,
    replay_service,
)
from chat_downloader.sites.kick import (
    resource_limits as limits,
)
from chat_downloader.sites.kick.errors import (
    KickCountryBlocked,
    KickResourceLimit,
    KickServerError,
)
from tests.kick_helpers import message_page, raw_message

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


@pytest.fixture
def history_server():
    state = SimpleNamespace(body=b"{}", status=200, headers={}, requests=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.requests += 1
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state.body)))
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                for offset in range(0, len(state.body), 4096):
                    self.wfile.write(state.body[offset : offset + 4096])
                    self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    state.url = f"http://127.0.0.1:{server.server_port}/messages"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.fixture(params=["requests", "curl", "cloudscraper"])
def backend(request):
    if request.param == "requests":
        session = requests.Session()
    elif request.param == "curl":
        session = CurlSession()
    else:
        session = cloudscraper.CloudScraper(disableCloudflareV1=True)
    session.trust_env = False
    try:
        yield session
    finally:
        session.close()


def _client(monkeypatch, server, backend):
    monkeypatch.setattr(api_client, "CHANNEL_MESSAGES_API", server.url)
    return api_client.KickApiClient(session=backend, timeout=(2, 2), trust_env=False)


def _raw(identifier="one", seconds=0, **fields):
    return raw_message(
        identifier, (START + timedelta(seconds=seconds)).isoformat(), **fields
    )


def _source(client, state, cancelled=None):
    return replay_service._iter_vod_messages(
        "1",
        START,
        END,
        ChatRequest(),
        api_client=client,
        diagnostics=state,
        cancelled=cancelled,
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_real_backends_bound_decoded_body_before_json(
    monkeypatch, history_server, backend, compressed
):
    body = json.dumps(message_page([_raw(content="synthetic-secret" * 1000)])).encode()
    history_server.body = gzip.compress(body) if compressed else body
    if compressed:
        history_server.headers = {"Content-Encoding": "gzip"}
    monkeypatch.setattr(history_response, "MAX_HISTORY_RESPONSE_BYTES", 128)
    client = _client(monkeypatch, history_server, backend)
    decoder = Mock(side_effect=AssertionError("oversized body reached JSON decoder"))
    monkeypatch.setattr(requests.Response, "json", decoder)
    state = {}
    with pytest.raises(KickResourceLimit, match=r"response byte.*incomplete") as caught:
        list(_source(client, state))
    assert "synthetic-secret" not in str(caught.value)
    decoder.assert_not_called()
    assert state["history_complete"] is False
    assert state["termination_reason"] == "error"
    assert history_server.requests == 1


def test_real_backends_accept_exact_history_response_boundary(
    monkeypatch, history_server, backend
):
    payload = message_page([_raw(content="valid \N{SNOWMAN}")])
    history_server.body = json.dumps(payload, ensure_ascii=False).encode()
    monkeypatch.setattr(
        history_response, "MAX_HISTORY_RESPONSE_BYTES", len(history_server.body)
    )
    client = _client(monkeypatch, history_server, backend)
    state = {}
    assert [item["message_id"] for item in _source(client, state)] == ["one"]
    assert state["history_complete"] is True


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (302, b"oversized" * 100, KickServerError),
        (423, b"<!doctype html>" * 100, KickCountryBlocked),
        (403, b"denied" * 100, CaptchaChallengeRequired),
        (503, b'{"ignored":"' + b"x" * 1000, KickServerError),
        (200, b"<!doctype html>" + b"x" * 1000, CaptchaChallengeRequired),
    ],
)
def test_real_backends_preserve_bounded_status_and_challenge_precedence(
    monkeypatch, history_server, backend, status, body, error
):
    history_server.status = status
    history_server.body = body
    history_server.headers = {"Retry-After": "7", "Location": "/not-followed"}
    monkeypatch.setattr(history_response, "MAX_HISTORY_RESPONSE_BYTES", 64)
    client = _client(monkeypatch, history_server, backend)
    with pytest.raises(error) as caught:
        client.fetch_message_page("1")
    if status == 503:
        assert caught.value.retry_after == 7
    assert history_server.requests == 1


@pytest.fixture
def spools(monkeypatch):
    opened = []
    factory = replay_service.tempfile.SpooledTemporaryFile

    def create(*args, **kwargs):
        spool = factory(*args, **kwargs)
        spool.write = Mock(wraps=spool.write)
        spool.readline = Mock(wraps=spool.readline)
        opened.append(spool)
        return spool

    monkeypatch.setattr(replay_service.tempfile, "SpooledTemporaryFile", create)
    return opened


def test_real_history_hashing_and_string_escaping_use_bounded_chunks(
    monkeypatch, history_server, backend, spools
):
    payload = message_page([_raw(ignored="\N{GRINNING FACE}" * 10000)])
    history_server.body = json.dumps(payload, ensure_ascii=False).encode()
    client = _client(monkeypatch, history_server, backend)
    original = json.dumps
    values = []

    def encode(value, *args, **kwargs):
        values.append(value)
        assert not isinstance(value, (dict, list))
        if isinstance(value, str):
            assert len(value) <= 2048
        return original(value, *args, **kwargs)

    monkeypatch.setattr(limits.json, "dumps", encode)
    assert [item["message_id"] for item in _source(client, {})] == ["one"]
    assert values
    assert spools[0].closed
    assert all(
        len(call.args[0]) <= 12 * 2048 for call in spools[0].write.call_args_list
    )
    assert all(
        call.args == (limits.MAX_REPLAY_RECORD_BYTES + 1,)
        for call in spools[0].readline.call_args_list
    )


@pytest.mark.parametrize(
    "phase",
    [
        "hash",
        "sort",
        "sort-final",
        "progress",
        "classify",
        "serialize",
        "serialize-final",
        "completion",
        "readback",
    ],
)
def test_real_history_cancellation_interrupts_page_work_and_cleans_spool(
    monkeypatch, history_server, backend, spools, phase
):
    records = (
        [_raw("one")]
        if phase in {"completion", "sort-final"}
        else [_raw("new", 2), _raw("old", 1)]
    )
    history_server.body = json.dumps(message_page(records)).encode()
    client = _client(monkeypatch, history_server, backend)
    cancelled = Event()
    if phase == "hash":
        original = limits.json.dumps

        def encode(value, *args, **kwargs):
            cancelled.set()
            return original(value, *args, **kwargs)

        monkeypatch.setattr(limits.json, "dumps", encode)
    elif phase in {"sort", "sort-final", "progress"}:
        original = replay_service._message_timestamp
        if phase == "progress":
            monkeypatch.setattr(replay_service, "monotonic", iter([0, 6]).__next__)

        def timestamp(raw):
            result = original(raw)
            cancelled.set()
            return result

        monkeypatch.setattr(replay_service, "_message_timestamp", timestamp)
    elif phase == "readback":
        original = replay_service.read_replay_record

        def read(spool):
            result = original(spool)
            cancelled.set()
            return result

        monkeypatch.setattr(replay_service, "read_replay_record", read)
    elif phase == "classify":
        original = replay_service._classify_message
        calls = []

        def classify(*args, **kwargs):
            calls.append(args)
            result = original(*args, **kwargs)
            cancelled.set()
            return result

        monkeypatch.setattr(replay_service, "_classify_message", classify)
    else:
        original = limits.ReplayBudget._write

        def write(self, spool, chunk):
            original(self, spool, chunk)
            if (
                phase == "serialize"
                or (phase == "serialize-final" and chunk == "}")
                or (phase == "completion" and chunk == "\n")
            ):
                cancelled.set()

        monkeypatch.setattr(limits.ReplayBudget, "_write", write)
    state = {}
    assert list(_source(client, state, cancelled)) == []
    assert cancelled.is_set()
    assert state["history_complete"] is False
    assert state["termination_reason"] == "interrupted"
    assert spools[0].closed
    if phase == "readback":
        spools[0].readline.assert_called_once()
    else:
        spools[0].readline.assert_not_called()
    if phase == "classify":
        assert len(calls) == 1
        spools[0].write.assert_not_called()
    if phase == "serialize":
        assert spools[0].write.call_count == 1


@pytest.mark.parametrize("limit", ["MAX_REPLAY_RECORD_BYTES", "MAX_REPLAY_PAGE_BYTES"])
def test_real_history_encoding_expansion_limits_are_terminal(
    monkeypatch, history_server, backend, spools, limit
):
    history_server.body = json.dumps(
        message_page([_raw(content="\N{LATIN SMALL LETTER E WITH ACUTE}" * 1000)]),
        ensure_ascii=False,
    ).encode()
    monkeypatch.setattr(limits, limit, 1024)
    client = _client(monkeypatch, history_server, backend)
    state = {}
    with pytest.raises(KickResourceLimit, match=r"serialization.*incomplete"):
        list(_source(client, state))
    assert state["history_complete"] is False
    assert spools[0].closed
    assert sum(len(call.args[0]) for call in spools[0].write.call_args_list) <= 1024


def test_spool_readback_rejects_oversized_record_before_decoding(monkeypatch):
    monkeypatch.setattr(limits, "MAX_REPLAY_RECORD_BYTES", 8)
    spool = io.BytesIO(b'{"ignored":"oversized"}\n')
    with pytest.raises(KickResourceLimit, match=r"record byte.*incomplete"):
        limits.read_replay_record(spool)
    assert spool.tell() == 9


def test_incremental_hash_preserves_canonical_json_contract():
    page = [{"z": [None, True, 1, 2.5, "\N{SNOWMAN}"], "a": {"empty": ""}}]
    expected = hashlib.sha256(json.dumps(page, sort_keys=True).encode()).digest()
    assert limits.replay_page_digest(page, None) == expected


def test_history_callback_overflow_is_latched_before_further_growth(monkeypatch):
    monkeypatch.setattr(history_response, "MAX_HISTORY_RESPONSE_BYTES", 2)
    body = history_response._HistoryBody()
    assert body.collect(b"abc") == 0
    assert body.content == b"ab"
    assert body.collect(b"must-not-append") == 0
    assert body.content == b"ab"
    assert body.exceeded


@pytest.mark.parametrize(
    ("overflow", "response_status", "expected"),
    [
        (False, None, OSError),
        (True, None, KickResourceLimit),
        (True, 200, KickResourceLimit),
        (True, 503, KickServerError),
    ],
)
def test_native_wrapper_cannot_hide_resource_failure_or_status_precedence(
    monkeypatch, overflow, response_status, expected
):
    monkeypatch.setattr(history_response, "MAX_HISTORY_RESPONSE_BYTES", 2)

    class NativeSession:
        __module__ = "curl_cffi.synthetic"

        def get(self, _url, **kwargs):
            if overflow:
                assert kwargs["content_callback"](b"abc") == 0
            response = None
            if response_status is not None:
                response = requests.Response()
                response.status_code = response_status
                response.headers["Retry-After"] = "7"
            raise requests.RequestException("native write failure", response=response)

    client = api_client.KickApiClient(
        session=NativeSession(), timeout=(2, 2), trust_env=False
    )
    with pytest.raises(expected) as caught:
        client.fetch_message_page("1")
    if response_status == 503:
        assert caught.value.retry_after == 7


def test_cancelled_json_encoder_stops_without_escaping_another_slice():
    cancelled = Event()
    encoded = limits._json_parts("x" * 10000, cancelled)
    assert next(encoded) == '"'
    assert len(next(encoded)) == 2048
    cancelled.set()
    assert list(encoded) == []
    assert limits.replay_page_digest([{}], cancelled) is None


def test_real_backend_bounded_hook_preserves_cookie_rotation(
    monkeypatch, history_server, backend
):
    payload = message_page([_raw()])
    history_server.body = json.dumps(payload).encode()
    history_server.headers = {
        "Set-Cookie": "kick_history_boundary=rotated; Path=/; HttpOnly"
    }
    client = _client(monkeypatch, history_server, backend)
    assert client.fetch_message_page("1") == payload
    assert backend.cookies.get("kick_history_boundary") == "rotated"


@pytest.mark.parametrize("oversized", [False, True])
def test_real_stream_hooks_keep_final_response_and_latch_prior_overflow(
    monkeypatch, history_server, oversized
):
    payload = message_page([_raw()])
    history_server.body = json.dumps(payload).encode()
    if oversized:
        monkeypatch.setattr(history_response, "MAX_HISTORY_RESPONSE_BYTES", 8)

    class ReissuedSession(requests.Session):
        def get(self, url, **kwargs):
            super().get(url, **kwargs)
            return super().get(url, **kwargs)

    with ReissuedSession() as backend:
        backend.trust_env = False
        client = _client(monkeypatch, history_server, backend)
        if oversized:
            with pytest.raises(KickResourceLimit):
                client.fetch_message_page("1")
        else:
            assert client.fetch_message_page("1") == payload
    assert history_server.requests == 2

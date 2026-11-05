# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from websocket import ABNF, WebSocketTimeoutException

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.sites.kick import (
    centrifugo_transport as ct,
)
from chat_downloader.sites.kick import (
    live_service,
    public_transport,
    replay_service,
)
from chat_downloader.sites.kick import (
    resource_limits as limits,
)
from chat_downloader.sites.kick import (
    websocket_transport as wt,
)
from chat_downloader.sites.kick.errors import KickResourceLimit
from chat_downloader.sites.models import Chat
from tests.kick_helpers import FakeWebSocket, load_fixture, message_page, raw_message
from tests.test_kick_connection_drop_unit import _handshake

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def _header(length, *, opcode=ABNF.OPCODE_TEXT, fin=True, masked=False):
    first = (128 if fin else 0) | opcode
    mask = 128 if masked else 0
    if length < 126:
        return bytes([first, mask | length])
    if length < 65536:
        return bytes([first, mask | 126]) + struct.pack("!H", length)
    return bytes([first, mask | 127]) + struct.pack("!Q", length)


def _frame(payload=b"", **kwargs):
    return _header(len(payload), **kwargs) + payload


def _wire_connection(wire):
    received = bytearray(wire)
    reads = []

    def recv(size):
        reads.append(size)
        result = bytes(received[:size])
        del received[:size]
        return result

    socket = MagicMock()
    socket.recv.side_effect = recv
    socket.gettimeout.return_value = 1
    connection = wt._BoundedWebSocket()
    connection.sock = socket
    connection.send = MagicMock()
    connection.pong = MagicMock()
    return connection, received, reads


@pytest.mark.parametrize("length", [126, 65536, 2**63])
@pytest.mark.parametrize("masked", [False, True])
def test_declared_length_rejected_before_mask_or_payload_reads(
    monkeypatch, length, masked
):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_BYTES", 125)
    header = _header(length, masked=masked)
    connection, received, reads = _wire_connection(header + b"synthetic-secret")
    with pytest.raises(KickResourceLimit, match=r"resource limit.*incomplete"):
        connection.recv()
    assert received == b"synthetic-secret"
    assert sum(reads) == len(header)
    # A caught failure must never permit the rejected declared payload to be read.
    with pytest.raises(KickResourceLimit):
        connection.recv()
    assert sum(reads) == len(header)


@pytest.mark.parametrize("length", [0, 1, 125, 126, 65536])
def test_legitimate_wire_boundary_frames_and_independent_messages(monkeypatch, length):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_BYTES", max(1, length))
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_FRAMES", 1)
    connection, received, _reads = _wire_connection(
        _frame(b"x" * length) + _frame(b"y" * length)
    )
    assert connection.recv() == "x" * length
    assert connection.recv() == "y" * length
    assert not received


@pytest.mark.parametrize("excess", [False, True])
def test_fragmented_totals_include_interleaved_control_payload(monkeypatch, excess):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_BYTES", 8)
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_FRAMES", 3)
    continuation = b"defg" if excess else b"def"
    connection, received, _reads = _wire_connection(
        _frame(b"abcd", fin=False)
        + _frame(b"!", opcode=ABNF.OPCODE_PING)
        + _frame(continuation, opcode=ABNF.OPCODE_CONT)
    )
    if excess:
        with pytest.raises(KickResourceLimit):
            connection.recv()
        assert received == continuation
    else:
        assert connection.recv() == "abcddef"
        assert not received
    connection.pong.assert_called_once_with(b"!")


@pytest.mark.parametrize("opcode", [ABNF.OPCODE_CONT, ABNF.OPCODE_PING])
def test_zero_length_fragment_and_control_floods_are_finite(monkeypatch, opcode):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_FRAMES", 3)
    connection, received, reads = _wire_connection(
        _frame(b"a", fin=False)
        + _frame(opcode=opcode, fin=opcode == ABNF.OPCODE_PING) * 3
        + b"unread"
    )
    with pytest.raises(KickResourceLimit):
        connection.recv()
    assert received == b"unread"
    assert len(reads) == 5  # Four frame headers, one initial payload.


def test_partial_payload_timeout_does_not_reserve_length_twice(monkeypatch):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_BYTES", 4)
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_FRAMES", 1)
    connection, _received, _reads = _wire_connection(b"")
    connection.sock.recv.side_effect = [
        _header(4),
        b"ab",
        TimeoutError(),
        b"cd",
        _header(4),
        b"next",
    ]
    with pytest.raises(WebSocketTimeoutException):
        connection.recv()
    assert connection.recv() == "abcd"
    assert connection.recv() == "next"


def test_recorded_large_payload_uses_real_bounded_websocket():
    payload = json.dumps(
        {"event": "history", "data": load_fixture("history_dense_recorded.json")}
    )
    connection, _received, _reads = _wire_connection(_frame(payload.encode()))
    transport = wt.KickPusherTransport(
        connector=lambda *_a, **_kw: connection, url="wss://example.test/"
    )
    transport.connect(1)
    assert transport.recv() == json.loads(payload)


def _centrifugo(raw):
    socket = FakeWebSocket([raw])
    transport = ct.KickCentrifugoTransport(
        client=SimpleNamespace(connection_token=lambda: "synthetic-token"),
        connector=lambda *_a, **_kw: socket,
        url="wss://example.test/",
    )
    transport.connect(1)
    return transport


def _publication(index):
    return json.dumps(
        {
            "push": {
                "channel": "chat",
                "pub": {"data": {"event": "Example", "data": index}},
            }
        }
    )


@pytest.mark.parametrize("binary", [False, True])
def test_centrifugo_publication_batch_limit_is_explicit(monkeypatch, binary):
    monkeypatch.setattr(ct, "MAX_CENTRIFUGO_BATCH_ENTRIES", 2)
    raw = "\n".join(_publication(index) for index in range(3))
    transport = _centrifugo(raw.encode() if binary else raw)
    with pytest.raises(KickResourceLimit, match=r"batch.*incomplete"):
        transport.recv()
    assert not transport._frames
    assert transport._ws is None
    with pytest.raises(KickResourceLimit, match="batch"):
        transport.recv()


def test_centrifugo_queue_limit_never_silently_discards_publications(monkeypatch):
    monkeypatch.setattr(ct, "MAX_CENTRIFUGO_QUEUED_FRAMES", 2)
    transport = _centrifugo("\n".join(_publication(index) for index in range(3)))
    with pytest.raises(KickResourceLimit, match=r"queue.*incomplete"):
        transport.recv()
    assert not transport._frames
    assert transport._ws is None
    with pytest.raises(KickResourceLimit, match="queue"):
        transport.recv()


def test_centrifugo_boundary_batch_drains_every_publication(monkeypatch):
    monkeypatch.setattr(ct, "MAX_CENTRIFUGO_BATCH_ENTRIES", 2)
    monkeypatch.setattr(ct, "MAX_CENTRIFUGO_QUEUED_FRAMES", 2)
    transport = _centrifugo("\n".join(_publication(index) for index in range(2)) + "\n")
    assert [transport.recv()["data"] for _ in range(2)] == [0, 1]


def test_resource_error_stops_public_reader_and_live_reconnect(monkeypatch):
    monkeypatch.setattr(wt, "MAX_WEBSOCKET_MESSAGE_BYTES", 4)
    connection, _received, _reads = _wire_connection(_header(5) + b"never")
    child = wt.KickPusherTransport(
        connector=lambda *_a, **_kw: connection, url="wss://example.test/"
    )
    child.connect(1)
    public = public_transport.KickPublicTransport(username="test", channel_id="1")
    public._receive_timeout = 0.001
    public._transports.append(child)
    client = MagicMock()
    public._read(child, client)
    client.close.assert_called_once()
    assert child._ws is None
    public.connect = MagicMock()
    public.subscribe = MagicMock()
    public.set_timeout = MagicMock()
    monkeypatch.setattr(
        live_service, "_iter_preloaded_chat", lambda *_a, **_kw: iter(())
    )
    factory = MagicMock(return_value=public)
    diagnostics = live_service._KickLiveDiagnostics()
    source = live_service._iter_chat_messages(
        SimpleNamespace(_http_timeout=(1, 1)),
        "test",
        "1",
        "2",
        ChatRequest(),
        diagnostics,
        transport_factory=factory,
    )
    with pytest.raises(KickResourceLimit, match=r"resource limit.*incomplete"):
        list(source)
    assert factory.call_count == 1
    assert diagnostics.summary["history_complete"] is False
    assert diagnostics.summary["websocket_reconnect_count"] == 0


@pytest.fixture
def tracked_spools(monkeypatch):
    created = []
    factory = replay_service.tempfile.SpooledTemporaryFile

    def create(*args, **kwargs):
        spool = factory(*args, **kwargs)
        spool.write = Mock(wraps=spool.write)
        created.append(spool)
        return spool

    monkeypatch.setattr(replay_service.tempfile, "SpooledTemporaryFile", create)
    return created


def _raw(identifier, seconds=0, **fields):
    return raw_message(
        identifier, (START + timedelta(seconds=seconds)).isoformat(), **fields
    )


def _replay(client, diagnostics, **options):
    return replay_service._iter_vod_messages(
        "1",
        START,
        END,
        ChatRequest(**options),
        api_client=client,
        diagnostics=diagnostics,
    )


@pytest.mark.parametrize(
    ("constant", "value", "pages", "calls", "message"),
    [
        (
            "MAX_REPLAY_PAGES",
            1,
            [
                message_page([_raw("new", 2)], cursor="next"),
                message_page([_raw("old", 1)]),
            ],
            1,
            "page/metadata",
        ),
        (
            "MAX_REPLAY_CURSOR_CHARS",
            16,
            [message_page([], cursor="synthetic-secret-cursor")],
            1,
            "cursor",
        ),
        (
            "MAX_REPLAY_CURSOR_BYTES",
            17,
            [message_page([], cursor="four")],
            1,
            "page/metadata",
        ),
        (
            "MAX_REPLAY_RECORDS",
            1,
            [message_page([_raw("one"), _raw("two")])],
            1,
            "record",
        ),
        (
            "MAX_REPLAY_SPOOL_BYTES",
            8,
            [message_page([_raw("one", content="synthetic-secret")])],
            1,
            "spool",
        ),
    ],
)
def test_replay_resource_budgets_fail_closed_before_growth(
    monkeypatch, tracked_spools, constant, value, pages, calls, message
):
    monkeypatch.setattr(limits, constant, value)
    client = MagicMock()
    client.fetch_message_page.side_effect = pages
    diagnostics = {}
    with pytest.raises(KickResourceLimit, match=message + ".*incomplete") as caught:
        list(_replay(client, diagnostics, max_messages=1))
    assert "synthetic-secret" not in str(caught.value)
    assert diagnostics["history_complete"] is False
    assert diagnostics["termination_reason"] == "error"
    assert client.fetch_message_page.call_count == calls
    assert tracked_spools
    assert all(spool.closed for spool in tracked_spools)
    if constant == "MAX_REPLAY_SPOOL_BYTES":
        written = sum(
            len(call.args[0]) for call in tracked_spools[0].write.call_args_list
        )
        assert written <= value


def test_replay_exact_spool_boundary_preserves_chronological_output(
    monkeypatch, tracked_spools
):
    raw = _raw("one", content="boundary \N{SNOWMAN}")
    parsed, _done = replay_service._classify_message(raw, START, END, {})
    size = len(json.dumps(parsed).encode()) + 1
    monkeypatch.setattr(limits, "MAX_REPLAY_SPOOL_BYTES", size)
    monkeypatch.setattr(limits, "MAX_REPLAY_PAGES", 1)
    monkeypatch.setattr(limits, "MAX_REPLAY_RECORDS", 1)
    client = MagicMock()
    client.fetch_message_page.return_value = message_page([raw])
    diagnostics = {}
    assert [row["message_id"] for row in _replay(client, diagnostics)] == ["one"]
    assert diagnostics["history_complete"] is True
    assert tracked_spools[0].closed


def test_replay_cancellation_discards_spooled_collection_and_closes_file(
    tracked_spools,
):
    client = MagicMock()
    diagnostics = {}
    source = replay_service.ReplaySource(
        "1",
        START,
        END,
        ChatRequest(),
        api_client=client,
        origin=START,
        diagnostics=diagnostics,
    )

    def fetch(*_args, **_kwargs):
        if client.fetch_message_page.call_count == 1:
            return message_page([_raw("newer", 2)], cursor="next")
        source.close()
        return message_page([_raw("older", 1)], cursor="another")

    client.fetch_message_page.side_effect = fetch
    assert list(source) == []
    assert source.cancelled.is_set()
    assert diagnostics["history_complete"] is False
    assert diagnostics["emitted_records"] == 0
    assert client.fetch_message_page.call_count == 2
    assert tracked_spools[0].write.called
    assert tracked_spools[0].closed


def test_cancellation_between_spooled_pages_does_not_emit_remaining_history(
    tracked_spools,
):
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        message_page([_raw("newer", 2)], cursor="next"),
        message_page([_raw("older", 1)]),
    ]
    cancelled = Event()
    state = {}
    source = replay_service._iter_vod_messages(
        "1",
        START,
        END,
        ChatRequest(),
        api_client=client,
        diagnostics=state,
        cancelled=cancelled,
    )
    assert next(source)["message_id"] == "older"
    cancelled.set()

    assert list(source) == []
    assert state["emitted_records"] == 1
    assert state["history_complete"] is False
    assert state["termination_reason"] == "interrupted"
    assert tracked_spools[0].closed


@pytest.mark.parametrize("kind", ["records", "spool"])
def test_replay_budgets_are_cumulative_across_pages(monkeypatch, tracked_spools, kind):
    newer, older = _raw("newer", 2), _raw("older", 1)
    if kind == "records":
        monkeypatch.setattr(limits, "MAX_REPLAY_RECORDS", 1)
    else:
        parsed, _done = replay_service._classify_message(newer, START, END, {})
        monkeypatch.setattr(
            limits, "MAX_REPLAY_SPOOL_BYTES", len(json.dumps(parsed).encode()) + 1
        )
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        message_page([newer], cursor="next"),
        message_page([older]),
    ]
    state = {}
    with pytest.raises(KickResourceLimit, match="incomplete"):
        list(_replay(client, state))
    assert client.fetch_message_page.call_count == 2
    assert state["history_complete"] is False
    assert state["emitted_records"] == 0
    assert tracked_spools[0].closed


def test_default_transport_rejects_oversized_wire_message(monkeypatch):
    client_socket, server_socket = socket.socketpair()
    client_socket.settimeout(2)
    server_socket.settimeout(2)

    def serve():
        with server_socket:
            _handshake(server_socket)
            server_socket.sendall(_header(limits.MAX_WEBSOCKET_MESSAGE_BYTES + 1))
            server_socket.recv(4096)

    monkeypatch.setattr(
        wt, "open_proxied_tls_socket", lambda *_args, **_kwargs: client_socket
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        served = pool.submit(serve)
        transport = wt.KickPusherTransport(url="wss://example.test/")
        try:
            transport.connect(2)
            transport.subscribe("2")
            with pytest.raises(KickResourceLimit):
                transport.recv()
        finally:
            transport.close()
        served.result(timeout=3)


@pytest.mark.parametrize("phase", ["preload", "backfill"])
def test_live_history_resource_failure_stops_without_fallback_requests(phase):
    client = MagicMock()
    failure = KickResourceLimit("Synthetic history resource limit.")
    if phase == "preload":
        client.fetch_preloaded_chat_state.side_effect = failure
    else:
        client.fetch_preloaded_chat_state.return_value = None
        client.fetch_message_page.side_effect = failure
    factory = MagicMock(return_value=MagicMock())
    diagnostics = live_service._KickLiveDiagnostics()
    source = live_service._iter_chat_messages(
        SimpleNamespace(_http_timeout=(1, 1), _kick_client=client),
        "test",
        "1",
        "2",
        ChatRequest(max_attempts=1, retry_timeout=0),
        diagnostics,
        transport_factory=factory,
        frame_iterator=lambda _transport: iter(
            [
                {
                    "event": "pusher_internal:subscription_succeeded",
                    "channel": "chatrooms.2.v2",
                }
            ]
        ),
    )
    with pytest.raises(KickResourceLimit):
        list(source)
    assert diagnostics.summary["history_complete"] is False
    assert diagnostics.summary["websocket_reconnect_count"] == 0
    assert factory.call_count == int(phase == "backfill")
    assert client.fetch_preloaded_chat_state.call_count == 1
    assert client.fetch_message_page.call_count == int(phase == "backfill")


def test_configured_message_limit_preserves_collected_history(tracked_spools):
    client = MagicMock()
    client.fetch_message_page.return_value = message_page(
        [_raw("newer", 2), _raw("older", 1)]
    )
    state = {}
    request = ChatRequest(max_messages=1, format="kick")
    source = replay_service.ReplaySource(
        "1",
        START,
        END,
        request,
        api_client=client,
        origin=START,
        diagnostics=state,
    )
    chat = Chat(source, diagnostics=state, id="video", status="past")
    configure_chat(chat, request, SimpleNamespace())
    assert [row["message_id"] for row in chat] == ["older"]
    assert state["termination_reason"] == "message_limit"
    assert state["history_complete"] is True
    assert state["emitted_records"] == 1
    assert tracked_spools[0].closed


@pytest.mark.parametrize("deadline", ["timeout", "inactivity_timeout"])
def test_late_fetch_cancellation_preserves_configured_deadline(
    deadline, tracked_spools
):
    entered, release = Event(), Event()
    client = MagicMock()

    def fetch(*_args, **_kwargs):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("Synthetic fetch was not released.")
        return message_page([_raw("late", 1)])

    client.fetch_message_page.side_effect = fetch
    state = {}
    request = ChatRequest(format="kick", **{deadline: 0.01})
    source = replay_service.ReplaySource(
        "1",
        START,
        END,
        request,
        api_client=client,
        origin=START,
        diagnostics=state,
    )
    chat = Chat(source, diagnostics=state, id="video", status="past")
    configure_chat(chat, request, SimpleNamespace())
    timed = chat.chat
    try:
        assert entered.wait(2)
        assert list(chat) == []
        assert state["termination_reason"] == deadline
    finally:
        release.set()
        timed._worker.join(2)
        chat.close()
    assert not timed._worker.is_alive()
    assert state["termination_reason"] == deadline
    assert state["history_complete"] is False
    assert tracked_spools[0].closed

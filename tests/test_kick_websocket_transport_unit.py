# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from websocket import (
    WebSocketBadStatusException,
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
)

from chat_downloader.sites.kick import websocket_transport as wt
from chat_downloader.sites.kick.constants import (
    PUSHER_PING,
    PUSHER_PONG,
    PUSHER_SUBSCRIBE,
)
from chat_downloader.sites.kick.websocket_transport import (
    KickPusherTransport,
    read_frames,
)
from tests.kick_helpers import FakeWebSocket


def _connected(ws, **kwargs):
    transport = KickPusherTransport(
        connector=lambda *_args, **_kwargs: ws, url="wss://fake.test/", **kwargs
    )
    transport.connect(5.0)
    return transport


@pytest.mark.parametrize(
    ("proxy", "failure"),
    [
        (None, False),
        ("socks5h://user:pass@proxy.test:1080", False),
        ("https://proxy.test:443", True),
    ],
)
def test_connector_tunnel_and_handshake_cleanup(monkeypatch, proxy, failure):
    socket = MagicMock()
    tunnel = MagicMock(return_value=socket)
    create = MagicMock(
        return_value="connection",
        side_effect=(WebSocketException("handshake failed") if failure else None),
    )
    monkeypatch.setattr(wt, "open_proxied_tls_socket", tunnel)
    monkeypatch.setattr(wt, "create_connection", create)
    if failure:
        with pytest.raises(WebSocketException, match="handshake failed"):
            wt._default_connector("wss://example.test/socket", None, proxy_url=proxy)
        socket.close.assert_called_once_with()
    else:
        assert (
            wt._default_connector("wss://example.test/socket", 4.0, proxy_url=proxy)
            == "connection"
        )
        tunnel.assert_called_once_with(
            "example.test", 443, timeout=4.0, proxy_url=proxy
        )
        assert create.call_args.kwargs["socket"] is socket


def test_connector_rejects_non_secure_proxied_url():
    with pytest.raises(OSError, match="Unsupported WebSocket"):
        wt._default_connector(
            "ws://example.test/socket", 4.0, proxy_url="http://proxy.test:8080"
        )


def test_direct_connector_supplies_socket_despite_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    direct_socket = MagicMock()
    tunnel = MagicMock(return_value=direct_socket)
    create = MagicMock(return_value="connected")
    monkeypatch.setattr(wt, "open_proxied_tls_socket", tunnel)
    monkeypatch.setattr(wt, "create_connection", create)
    assert wt._default_connector("wss://example.test/socket", 2.0) == "connected"
    tunnel.assert_called_once_with("example.test", 443, timeout=2.0, proxy_url=None)
    assert create.call_args.kwargs["socket"] is direct_socket


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (WebSocketException(), None),
        (WebSocketBadStatusException("forbidden", 403), r"HTTP 403.*try --proxy"),
        (None, "returned no connection"),
    ],
)
def test_connect_failure(error, match):
    transport = KickPusherTransport(
        connector=MagicMock(side_effect=error, return_value=None)
    )
    with pytest.raises(ConnectionError, match=match):
        transport.connect(1.0)


def test_connect_can_force_key_rediscovery(monkeypatch):
    resolver = MagicMock(return_value="wss://fresh.example/app/key")
    connector = MagicMock(return_value=FakeWebSocket())
    monkeypatch.setattr(wt, "get_pusher_ws_url", resolver)
    KickPusherTransport(connector=connector).connect(1.0, force_discover=True)
    resolver.assert_called_once_with(force_discover=True, http_client=None)
    assert connector.call_args.args[0] == "wss://fresh.example/app/key"


@pytest.mark.parametrize("broken", [False, True])
def test_timeout_and_close_before_and_after_connection(broken):
    ws = FakeWebSocket()
    transport = KickPusherTransport(connector=lambda *_args, **_kwargs: ws)
    transport.set_timeout(2.0)
    transport.close()
    assert ws.timeout is None
    assert ws.closed is False
    transport.connect(5.0)
    if broken:
        ws.settimeout = MagicMock(side_effect=OSError("bad socket"))
        with pytest.raises(ConnectionError, match="configure"):
            transport.set_timeout(4)
    else:
        transport.set_timeout(2.0)
        assert ws.timeout == 2.0
    transport.close()
    assert ws.closed is True


@pytest.mark.parametrize(
    ("operation", "args", "event", "data"),
    [
        (
            "subscribe",
            ("54321",),
            PUSHER_SUBSCRIBE,
            {"auth": "", "channel": "chatrooms.54321.v2"},
        ),
        ("send_pong", (), PUSHER_PONG, None),
    ],
)
def test_outbound_frames(operation, args, event, data):
    ws = FakeWebSocket()
    getattr(_connected(ws), operation)(*args)
    sent = json.loads(ws.sent[0])
    assert sent["event"] == event
    if data is not None:
        assert sent["data"] == data


@pytest.mark.parametrize(
    ("operation", "connected"),
    [
        ("send_pong", False),
        ("recv", False),
        ("send_pong", True),
    ],
)
def test_io_without_connection_or_with_broken_sender(operation, connected):
    ws = FakeWebSocket(send_error=OSError("broken"))
    transport = _connected(ws) if connected else KickPusherTransport()
    with pytest.raises(ConnectionError):
        getattr(transport, operation)()


@pytest.mark.parametrize(
    ("raw", "closed"),
    [
        (TimeoutError(), False),
        (WebSocketTimeoutException(), False),
        (WebSocketConnectionClosedException(), True),
        (OSError("closed"), True),
        ("", True),
    ],
)
def test_recv_timeouts_and_disconnects(raw, closed):
    transport = _connected(FakeWebSocket([raw]))
    if closed:
        with pytest.raises(ConnectionError):
            transport.recv()
    else:
        assert transport.recv() is None


@pytest.mark.parametrize("count_diagnostics", [False, True])
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("{not json", "invalid JSON"),
        ("[1, 2, 3]", "decoded frame was not an object"),
    ],
)
def test_recv_invalid_frame_is_captured(monkeypatch, raw, reason, count_diagnostics):
    captured, diagnostics = MagicMock(), []
    monkeypatch.setattr(wt, "capture_debug_sample", captured)
    transport = _connected(
        FakeWebSocket([raw]),
        diagnostic_callback=diagnostics.append if count_diagnostics else None,
    )
    assert transport.recv() is None
    assert captured.call_args.args[0] == "kick-unknown-websocket-shape"
    assert captured.call_args.args[1]["reason"] == reason
    assert captured.call_args.kwargs["sample_limit"] == 10
    if reason != "invalid JSON":
        assert captured.call_args.args[1] == {"raw": [1, 2, 3], "reason": reason}
    assert diagnostics == (
        ["invalid_websocket_frame_count"] if count_diagnostics else []
    )


def test_close_ignores_errors():
    _connected(FakeWebSocket(close_error=OSError("nope"))).close()


def test_read_frames_handles_ping_skips_and_yields():
    ping = {"event": PUSHER_PING}
    message = {"event": "App\\Events\\ChatMessageEvent", "data": "{}"}
    assert _connected(FakeWebSocket([json.dumps(message)])).recv() == message
    ws = FakeWebSocket(
        [
            TimeoutError(),
            json.dumps(ping),
            json.dumps(message),
            WebSocketConnectionClosedException(),
        ]
    )
    frames = []
    with pytest.raises(ConnectionError):
        for frame in read_frames(_connected(ws)):
            frames.append(frame)  # noqa: PERF402 — iteration raises
    assert frames == [ping, message]
    assert json.loads(ws.sent[0])["event"] == PUSHER_PONG


def test_idle_watchdog_reconnects_after_repeated_timeouts():
    transport = _connected(FakeWebSocket([TimeoutError(), TimeoutError()]))
    with (
        patch.object(wt.time, "monotonic", side_effect=[0.0, 179.0, 180.0]),
        pytest.raises(ConnectionError, match="became idle"),
    ):
        next(read_frames(transport))

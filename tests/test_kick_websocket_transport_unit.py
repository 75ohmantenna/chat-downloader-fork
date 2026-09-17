# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from typing import Any
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


def _connected(ws: FakeWebSocket, **kwargs: Any) -> KickPusherTransport:
    transport = KickPusherTransport(
        connector=lambda _url, _timeout, **_kwargs: ws,
        url="wss://fake.test/",
        **kwargs,
    )
    transport.connect(5.0)
    return transport


@pytest.mark.parametrize("proxy", [None, "socks5h://user:pass@proxy.test:1080"])
def test_default_connector_creates_direct_or_authenticated_tunnel(monkeypatch, proxy):
    proxy_socket = MagicMock()
    tunnel = MagicMock(return_value=proxy_socket)
    create = MagicMock(return_value="connection")
    monkeypatch.setattr(wt, "open_proxied_tls_socket", tunnel)
    monkeypatch.setattr(wt, "create_connection", create)
    assert (
        wt._default_connector("wss://example.test/socket", 4.0, proxy_url=proxy)
        == "connection"
    )
    if proxy:
        tunnel.assert_called_once_with(
            "example.test", 443, timeout=4.0, proxy_url=proxy
        )
        assert create.call_args.kwargs["socket"] is proxy_socket
    else:
        create.assert_called_once_with("wss://example.test/socket", timeout=4.0)


def test_default_connector_rejects_non_secure_proxied_url() -> None:
    with pytest.raises(OSError, match="Unsupported proxied WebSocket"):
        wt._default_connector(
            "ws://example.test/socket",
            4.0,
            proxy_url="http://proxy.test:8080",
        )


def test_default_connector_closes_proxy_socket_on_handshake_failure(
    monkeypatch: Any,
) -> None:
    proxy_socket = MagicMock()
    monkeypatch.setattr(
        wt,
        "open_proxied_tls_socket",
        MagicMock(return_value=proxy_socket),
    )
    monkeypatch.setattr(
        wt,
        "create_connection",
        MagicMock(side_effect=WebSocketException("handshake failed")),
    )

    with pytest.raises(WebSocketException, match="handshake failed"):
        wt._default_connector(
            "wss://example.test/socket",
            None,
            proxy_url="https://proxy.test:443",
        )

    proxy_socket.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (WebSocketException(), None),
        (WebSocketBadStatusException("forbidden", 403), r"HTTP 403.*try --proxy"),
        (None, "returned no connection"),
    ],
)
def test_connect_failure(error, match) -> None:
    transport = KickPusherTransport(
        connector=MagicMock(side_effect=error, return_value=None)
    )
    with pytest.raises(ConnectionError, match=match):
        transport.connect(1.0)


def test_connect_can_force_pusher_key_rediscovery(monkeypatch: Any) -> None:
    ws = FakeWebSocket()
    resolver = MagicMock(return_value="wss://fresh.example/app/key")
    monkeypatch.setattr(wt, "get_pusher_ws_url", resolver)
    transport = KickPusherTransport(
        connector=lambda _url, _timeout, **_kwargs: ws,
    )

    transport.connect(1.0, force_discover=True)

    resolver.assert_called_once_with(
        force_discover=True,
        http_client=None,
    )


def test_set_timeout_and_close_before_connection_then_configure_socket() -> None:
    ws = FakeWebSocket()
    transport = KickPusherTransport(connector=lambda *_args, **_kwargs: ws)
    transport.set_timeout(2.0)
    transport.close()
    assert ws.timeout is None
    assert ws.closed is False
    transport.connect(5.0)
    transport.set_timeout(2.0)
    assert ws.timeout == 2.0


def test_set_timeout_failure_is_retryable_and_close_releases_socket() -> None:
    ws = FakeWebSocket()
    ws.settimeout = MagicMock(side_effect=OSError("bad socket"))
    transport = _connected(ws)

    with pytest.raises(ConnectionError, match="configure"):
        transport.set_timeout(4)

    transport.close()
    assert ws.closed is True


def test_subscribe_sends_expected_frame() -> None:
    ws = FakeWebSocket()
    transport = _connected(ws)
    transport.subscribe("54321")
    sent = json.loads(ws.sent[0])
    assert sent["event"] == PUSHER_SUBSCRIBE
    assert sent["data"] == {"auth": "", "channel": "chatrooms.54321.v2"}


def test_send_pong_frame() -> None:
    ws = FakeWebSocket()
    transport = _connected(ws)
    transport.send_pong()
    assert json.loads(ws.sent[0])["event"] == PUSHER_PONG


@pytest.mark.parametrize("operation", ["send_pong", "recv"])
def test_io_before_connect_raises(operation) -> None:
    transport = KickPusherTransport(connector=lambda *_args, **_kwargs: FakeWebSocket())
    with pytest.raises(ConnectionError):
        getattr(transport, operation)()


def test_send_failure_raises_connection_error() -> None:
    ws = FakeWebSocket(send_error=OSError("broken"))
    transport = _connected(ws)
    with pytest.raises(ConnectionError):
        transport.send_pong()


@pytest.mark.parametrize("error", [TimeoutError(), WebSocketTimeoutException()])
def test_recv_timeout_returns_none(error: Exception) -> None:
    transport = _connected(FakeWebSocket([error]))
    assert transport.recv() is None


@pytest.mark.parametrize(
    "raw", [WebSocketConnectionClosedException(), OSError("closed"), ""]
)
def test_recv_closed_or_empty_raises(raw) -> None:
    transport = _connected(FakeWebSocket([raw]))
    with pytest.raises(ConnectionError):
        transport.recv()


@pytest.mark.parametrize("count_diagnostics", [False, True])
@pytest.mark.parametrize(
    ("raw", "reason"),
    [("{not json", "invalid JSON"), ("[1, 2, 3]", "decoded frame was not an object")],
)
def test_recv_invalid_frame_is_captured_and_counted(
    monkeypatch, raw, reason, count_diagnostics
):
    captured = MagicMock()
    monkeypatch.setattr(wt, "capture_debug_sample", captured)
    diagnostics = []
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


def test_recv_valid_frame_returns_dict() -> None:
    frame = {"event": "App\\Events\\ChatMessageEvent", "data": "{}"}
    transport = _connected(FakeWebSocket([json.dumps(frame)]))
    assert transport.recv() == frame


def test_close_closes_socket() -> None:
    ws = FakeWebSocket()
    transport = _connected(ws)
    transport.close()
    assert ws.closed is True


def test_close_ignores_errors() -> None:
    ws = FakeWebSocket(close_error=OSError("nope"))
    transport = _connected(ws)
    transport.close()  # error swallowed


def test_read_frames_handles_ping_and_skips_and_yields() -> None:
    ping = {"event": PUSHER_PING}
    message = {"event": "App\\Events\\ChatMessageEvent", "data": "{}"}
    ws = FakeWebSocket(
        [
            TimeoutError(),  # -> recv returns None -> skipped
            json.dumps(ping),  # -> ping answered, then yielded for diagnostics
            json.dumps(message),  # -> yielded
            WebSocketConnectionClosedException(),  # -> ends the generator
        ]
    )
    transport = _connected(ws)

    frames = []
    with pytest.raises(ConnectionError):
        for frame in read_frames(transport):
            frames.append(frame)  # noqa: PERF402 — generator raises, cannot use list()

    assert frames == [ping, message]
    assert json.loads(ws.sent[0])["event"] == PUSHER_PONG


def test_read_frames_idle_watchdog_reconnects_after_repeated_timeouts() -> None:
    ws = FakeWebSocket([TimeoutError(), TimeoutError()])
    transport = _connected(ws)

    with (
        patch.object(wt.time, "monotonic", side_effect=[0.0, 179.0, 180.0]),
        pytest.raises(ConnectionError, match="became idle"),
    ):
        next(read_frames(transport))

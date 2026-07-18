# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from websocket import (
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


def _connected(ws: FakeWebSocket) -> KickPusherTransport:
    transport = KickPusherTransport(
        connector=lambda _url, _timeout, **_kwargs: ws,
        url="wss://fake.test/",
    )
    transport.connect(5.0)
    return transport


def test_default_connector_invokes_create_connection(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_create(url: str, timeout: float | None, **kwargs: Any) -> str:
        captured["url"] = url
        captured["timeout"] = timeout
        return "connection"

    monkeypatch.setattr(wt, "create_connection", fake_create)
    result = wt._default_connector("wss://example", 3.0)
    assert result == "connection"
    assert captured == {"url": "wss://example", "timeout": 3.0}


def test_default_connector_opens_authenticated_proxy_tunnel(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}
    proxy_socket = MagicMock()

    monkeypatch.setattr(
        wt,
        "open_proxied_tls_socket",
        MagicMock(return_value=proxy_socket),
    )

    def fake_create(url: str, timeout: float | None, **kwargs: Any) -> str:
        captured.update(url=url, timeout=timeout, **kwargs)
        return "connection"

    monkeypatch.setattr(wt, "create_connection", fake_create)

    result = wt._default_connector(
        "wss://example.test/socket",
        4.0,
        proxy_url="socks5h://user:pass@proxy.test:1080",
    )

    assert result == "connection"
    wt.open_proxied_tls_socket.assert_called_once_with(
        "example.test",
        443,
        timeout=4.0,
        proxy_url="socks5h://user:pass@proxy.test:1080",
    )
    assert captured["socket"] is proxy_socket


def test_default_connector_rejects_non_secure_proxied_url() -> None:
    with pytest.raises(OSError, match="Unsupported proxied websocket"):
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


def test_connect_failure_raises_connection_error() -> None:
    def boom(_url: str, _timeout: float | None, **_kwargs: Any) -> Any:
        raise WebSocketException

    transport = KickPusherTransport(connector=boom)
    with pytest.raises(ConnectionError):
        transport.connect(1.0)


def test_connect_rejects_missing_websocket_object() -> None:
    transport = KickPusherTransport(connector=lambda *_args, **_kwargs: None)

    with pytest.raises(ConnectionError, match="returned no connection"):
        transport.connect(1.0)


def test_set_timeout_noop_before_connect() -> None:
    transport = KickPusherTransport(connector=lambda _u, _t, **_kwargs: FakeWebSocket())
    # No connection yet: must not raise.
    transport.set_timeout(2.0)


def test_set_timeout_after_connect() -> None:
    ws = FakeWebSocket()
    transport = _connected(ws)
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


def test_send_before_connect_raises() -> None:
    transport = KickPusherTransport(connector=lambda _u, _t, **_kwargs: FakeWebSocket())
    with pytest.raises(ConnectionError):
        transport.send_pong()


def test_send_failure_raises_connection_error() -> None:
    ws = FakeWebSocket(send_error=OSError("broken"))
    transport = _connected(ws)
    with pytest.raises(ConnectionError):
        transport.send_pong()


def test_recv_before_connect_raises() -> None:
    transport = KickPusherTransport(connector=lambda _u, _t, **_kwargs: FakeWebSocket())
    with pytest.raises(ConnectionError):
        transport.recv()


@pytest.mark.parametrize("error", [TimeoutError(), WebSocketTimeoutException()])
def test_recv_timeout_returns_none(error: Exception) -> None:
    transport = _connected(FakeWebSocket([error]))
    assert transport.recv() is None


@pytest.mark.parametrize(
    "error", [WebSocketConnectionClosedException(), OSError("closed")]
)
def test_recv_closed_raises_connection_error(error: Exception) -> None:
    transport = _connected(FakeWebSocket([error]))
    with pytest.raises(ConnectionError):
        transport.recv()


def test_recv_empty_payload_raises_connection_error() -> None:
    transport = _connected(FakeWebSocket([""]))
    with pytest.raises(ConnectionError):
        transport.recv()


def test_recv_malformed_frame_returns_none() -> None:
    transport = _connected(FakeWebSocket(["{not json"]))
    assert transport.recv() is None


def test_recv_non_object_frame_returns_none() -> None:
    transport = _connected(FakeWebSocket(["[1, 2, 3]"]))
    assert transport.recv() is None


def test_recv_valid_frame_returns_dict() -> None:
    frame = {"event": "App\\Events\\ChatMessageEvent", "data": "{}"}
    transport = _connected(FakeWebSocket([json.dumps(frame)]))
    assert transport.recv() == frame


def test_close_before_connect_is_noop() -> None:
    transport = KickPusherTransport(connector=lambda _u, _t, **_kwargs: FakeWebSocket())
    transport.close()  # must not raise


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
            json.dumps(ping),  # -> ping answered, continue
            json.dumps(message),  # -> yielded
            WebSocketConnectionClosedException(),  # -> ends the generator
        ]
    )
    transport = _connected(ws)

    frames = []
    with pytest.raises(ConnectionError):
        for frame in read_frames(transport):
            frames.append(frame)  # noqa: PERF402 — generator raises, cannot use list()

    assert frames == [message]
    assert json.loads(ws.sent[0])["event"] == PUSHER_PONG


def test_read_frames_idle_watchdog_reconnects_after_repeated_timeouts() -> None:
    ws = FakeWebSocket([TimeoutError(), TimeoutError()])
    transport = _connected(ws)

    with (
        patch.object(wt.time, "monotonic", side_effect=[0.0, 179.0, 180.0]),
        pytest.raises(ConnectionError, match="became idle"),
    ):
        next(read_frames(transport))

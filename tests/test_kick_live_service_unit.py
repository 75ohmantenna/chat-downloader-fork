# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    InvalidParameter,
    RetriesExceeded,
)
from chat_downloader.formatting import ItemFormatter
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import live_service
from chat_downloader.sites.kick.api_client import PreloadedChatState
from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    MESSAGE_DELETED_EVENT,
    PINNED_MESSAGE_CREATED_EVENT,
    PUSHER_ERROR,
    SUBSCRIPTION_EVENT,
)
from chat_downloader.sites.kick.errors import KickError
from tests.kick_helpers import (
    FakeDownloader,
    FakeKickSession,
    FakeResponse,
    FakeTransport,
    load_fixture,
    make_frame_iterator,
    pusher_frame,
)


def _request(**overrides: Any) -> ChatRequest:
    params: dict[str, Any] = {
        "url": "https://kick.com/examplechannel",
        "max_attempts": 2,
        "retry_timeout": 0,
        "interruptible_retry": False,
    }
    params.update(overrides)
    return ChatRequest.from_kwargs(**params)


class _NoRetryDownloader:
    """Downloader whose ``retry`` never sleeps or raises (drives exhaustion)."""

    @staticmethod
    def retry(*_args: Any, **_kwargs: Any) -> None:
        return None

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self._http_timeout = (10.0, 30.0)
        self._kick_api_client: Any | None = None

    @property
    def _kick_client(self) -> Any:
        if self._kick_api_client is None:
            from chat_downloader.sites.kick.api_client import KickApiClient

            self._kick_api_client = KickApiClient(timeout=self._http_timeout)
        return self._kick_api_client

    def _session_get(self, _url: str, **_kwargs: Any) -> Any:
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _DownloaderWithProxy:
    """Minimal downloader stub with a session that has proxy configured."""

    def __init__(self) -> None:
        self.session = MagicMock()
        self.session.proxies = {"https": "http://proxy.example:8080"}
        self.session.trust_env = False


class _DownloaderWithEmptyProxy:
    """Downloader with a session but no proxy configured."""

    def __init__(self) -> None:
        self.session = MagicMock()
        self.session.proxies = {}
        self.session.trust_env = False


# ── _resolve_ws_proxy ─────────────────────────────────────────────────────────


def test_resolve_ws_proxy_returns_complete_url() -> None:
    downloader = _DownloaderWithProxy()
    assert live_service._resolve_ws_proxy(downloader) == "http://proxy.example:8080"


def test_resolve_ws_proxy_returns_none_for_empty_proxies() -> None:
    assert live_service._resolve_ws_proxy(_DownloaderWithEmptyProxy()) is None


def test_resolve_ws_proxy_returns_none_without_session() -> None:
    assert live_service._resolve_ws_proxy(object()) is None


def test_resolve_ws_proxy_returns_none_for_empty_url() -> None:
    """Proxies with empty string value yields None."""
    downloader = _DownloaderWithEmptyProxy()
    downloader.session.proxies = {"https": ""}
    assert live_service._resolve_ws_proxy(downloader) is None


# ── _resolve_channel ──────────────────────────────────────────────────────────


def test_resolve_channel_live() -> None:
    data = load_fixture("channel_live.json")
    channel_id, chatroom_id, title = live_service._resolve_channel(
        data, "examplechannel"
    )
    assert channel_id == "12345"
    assert chatroom_id == "54321"
    assert title == "Example live stream title"


def test_resolve_channel_missing_channel_id() -> None:
    with pytest.raises(KickError, match="channel id"):
        live_service._resolve_channel({"chatroom": {"id": 1}}, "x")


def test_resolve_channel_missing_chatroom_id() -> None:
    data = load_fixture("channel_missing_chatroom.json")
    with pytest.raises(KickError, match="chatroom id"):
        live_service._resolve_channel(data, "examplechannel")


def test_resolve_channel_rejects_non_numeric_ids() -> None:
    with pytest.raises(KickError, match="non-numeric"):
        live_service._resolve_channel(
            {"id": "../channel", "chatroom": {"id": "room"}},
            "examplechannel",
        )


def test_resolve_channel_offline() -> None:
    data = load_fixture("channel_offline.json")
    channel_id, chatroom_id, title = live_service._resolve_channel(
        data, "examplechannel"
    )
    assert channel_id == "12345"
    assert chatroom_id == "54321"
    assert title == "examplechannel"


def test_resolve_channel_live_without_title_falls_back_to_username() -> None:
    data = {"id": 1, "chatroom": {"id": 2}, "livestream": {}}
    _cid, _rid, title = live_service._resolve_channel(data, "fallbackname")
    assert title == "fallbackname"


# ── _fetch_channel_with_retry ─────────────────────────────────────────────────


def test_fetch_channel_with_retry_succeeds_after_transient() -> None:
    payload = load_fixture("channel_live.json")
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(503, {"e": 1}), FakeResponse(200, payload)])
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        data = live_service._fetch_channel_with_retry(
            downloader, "examplechannel", _request()
        )
    assert data["id"] == 12345


def test_fetch_channel_with_retry_exhausts() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(500, {"e": 1})])
    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        pytest.raises(RetriesExceeded),
    ):
        live_service._fetch_channel_with_retry(
            downloader, "x", _request(max_attempts=1)
        )


def test_fetch_channel_with_retry_unreachable_guard() -> None:
    # retry that never raises lets the loop exhaust, hitting the guard.
    downloader = _NoRetryDownloader()
    session = FakeKickSession([FakeResponse(500, {"e": 1})])
    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        pytest.raises(RuntimeError, match="unreachable"),
    ):
        live_service._fetch_channel_with_retry(
            downloader, "x", _request(max_attempts=1)
        )


# ── _open_subscribed_transport ────────────────────────────────────────────────


def test_open_subscribed_transport_retries_then_succeeds() -> None:
    transport = FakeTransport(connect_errors=1)
    opened = live_service._open_subscribed_transport(
        FakeDownloader(), "54321", _request(), lambda: transport
    )
    assert opened is transport
    assert transport.connected is True
    assert transport.subscribed_to == "54321"
    assert transport.close_count == 1  # the failed attempt closed its transport


def test_open_subscribed_transport_separates_connect_and_receive_timeouts() -> None:
    transport = FakeTransport()
    downloader = FakeDownloader(connect_timeout=7.5, read_timeout=22.0)
    with patch.object(live_service, "log") as mock_log:
        opened = live_service._open_subscribed_transport(
            downloader,
            "54321",
            _request(message_receive_timeout=0.1),
            lambda: transport,
        )

    assert opened is transport
    assert transport.connect_timeout == pytest.approx(7.5)
    assert transport.receive_timeout == pytest.approx(1.0)
    assert transport.subscribed_to == "54321"
    mock_log.assert_called_once_with(
        "debug",
        "Kick WebSocket receive timeout: requested=0.1s, effective=1.0s.",
    )


def test_open_subscribed_transport_logs_unclamped_receive_timeout() -> None:
    transport = FakeTransport()
    with patch.object(live_service, "log") as mock_log:
        live_service._open_subscribed_transport(
            FakeDownloader(),
            "54321",
            _request(message_receive_timeout=2.5),
            lambda: transport,
        )

    assert transport.receive_timeout == pytest.approx(2.5)
    mock_log.assert_called_once_with(
        "debug",
        "Kick WebSocket receive timeout: requested=2.5s, effective=2.5s.",
    )


def test_open_subscribed_transport_unreachable_guard() -> None:
    with pytest.raises(RuntimeError, match="unreachable"):
        live_service._open_subscribed_transport(
            _NoRetryDownloader(),
            "1",
            _request(max_attempts=1),
            lambda: FakeTransport(connect_errors=5),
        )


def test_open_subscribed_transport_preserves_terminal_connection_error() -> None:
    with pytest.raises(
        RetriesExceeded,
        match="Last Kick WebSocket error: fake connect failure",
    ):
        live_service._open_subscribed_transport(
            FakeDownloader(),
            "1",
            _request(max_attempts=1),
            lambda: FakeTransport(connect_errors=5),
        )


# ── end-to-end via get_chat_by_channel ────────────────────────────────────────


def _build_chat(downloader: FakeDownloader, **kwargs: Any) -> Any:
    return live_service.get_chat_by_channel(
        downloader,
        "examplechannel",
        _request(**kwargs.pop("request_kwargs", {})),
        **kwargs,
    )


def test_get_chat_by_channel_emits_preloaded_then_live() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )
    live_data = load_fixture("chat_message_event_data.json")
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]]
            ),
        )
        assert chat.title == "Example live stream title"
        assert chat.status == "live"
        messages = list(chat.chat)
        ids = [m["message_id"] for m in messages]
        assert ids == ["preloaded-1", "preloaded-2", "live-1"]


def test_get_chat_by_channel_default_transport_binds_diagnostics() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    transports: list[FakeTransport] = []
    callbacks: list[Any] = []

    def transport_factory(*, diagnostic_callback: Any) -> FakeTransport:
        callbacks.append(diagnostic_callback)
        transport = FakeTransport()
        transports.append(transport)
        return transport

    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "live", "type": "message", "content": "message"},
    )
    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service,
            "KickPusherTransport",
            side_effect=transport_factory,
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            frame_iterator=make_frame_iterator([[frame]]),
        )
        assert [message["message_id"] for message in chat.chat] == ["live"]

    assert len(transports) == 1
    assert len(callbacks) == 1
    callbacks[0]("invalid_websocket_frame_count")
    assert chat.diagnostics["websocket_frame_count"] == 1
    assert chat.diagnostics["invalid_websocket_frame_count"] == 1


def test_successful_frame_capture_requires_explicit_scope_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", raising=False)
    captured = []
    monkeypatch.setattr(
        live_service,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "live", "content": "message"},
    )

    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([[frame]]),
        )
        assert [message["message_id"] for message in chat.chat] == ["live"]

    assert captured == []


def test_successful_frame_capture_is_bounded_across_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", "yes")
    captured = []
    monkeypatch.setattr(
        live_service,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    message_frames = [
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": str(index), "type": "message", "content": f"message {index}"},
        )
        for index in range(5)
    ]
    subscription_frames = [
        pusher_frame(
            SUBSCRIPTION_EVENT,
            {"id": f"sub-{index}", "content": f"subscription {index}"},
        )
        for index in range(4)
    ]
    control_frame = {"event": "pusher:connection_established", "data": "{}"}
    unknown_frame = {"event": "App\\Events\\FutureEvent", "data": "{}"}
    malformed_frame = {"event": CHAT_MESSAGE_EVENT, "data": "not JSON"}

    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [
                        control_frame,
                        unknown_frame,
                        malformed_frame,
                        *message_frames[:2],
                        *subscription_frames[:2],
                        ConnectionError("drop"),
                    ],
                    [*message_frames[2:], *subscription_frames[2:]],
                ]
            ),
        )
        messages = list(chat.chat)
        assert [message["message_id"] for message in messages] == [
            "0",
            "1",
            "sub-0",
            "sub-1",
            "2",
            "3",
            "4",
            "sub-2",
            "sub-3",
        ]

    successful_captures = [
        call for call in captured if call[0][0].startswith("kick-websocket-frame-")
    ]
    assert successful_captures == [
        (
            ("kick-websocket-frame-text-message", frame),
            {"sample_limit": 3},
        )
        for frame in message_frames[:2]
    ] + [
        (
            ("kick-websocket-frame-subscription", frame),
            {"sample_limit": 3},
        )
        for frame in subscription_frames[:2]
    ] + [
        (
            ("kick-websocket-frame-text-message", frame),
            {"sample_limit": 3},
        )
        for frame in message_frames[2:3]
    ] + [
        (
            ("kick-websocket-frame-subscription", frame),
            {"sample_limit": 3},
        )
        for frame in subscription_frames[2:3]
    ]


def test_successful_frame_capture_writes_independent_type_samples(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    caplog.set_level("DEBUG", logger=live_service.logger.name)
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    message_frames = [
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": f"msg-{index}", "type": "message", "content": "message"},
        )
        for index in range(4)
    ]
    subscription_frames = [
        pusher_frame(
            SUBSCRIPTION_EVENT,
            {"id": f"sub-{index}", "content": "subscription"},
        )
        for index in range(4)
    ]

    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[*message_frames, *subscription_frames]]
            ),
        )
        assert len(list(chat.chat)) == 8

    assert len(list(sample_dir.glob("kick-websocket-frame-text-message-*.json"))) == 3
    assert len(list(sample_dir.glob("kick-websocket-frame-subscription-*.json"))) == 3


def test_get_chat_by_channel_emits_current_pin_after_preloaded_history() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
        ]
    )
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages", "pins"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([[]]),
        )

        messages = list(chat.chat)

    assert [message["message_type"] for message in messages] == [
        "text_message",
        "pinned_message",
    ]
    assert messages[1]["message_id"] == "kick-pin:startup-pinned-message"
    assert messages[1]["metadata"]["pinned_by"]["display_name"] == ("StartupModerator")
    assert isinstance(
        messages[1]["metadata"]["original_message_created_at"],
        int,
    )
    assert isinstance(messages[1]["metadata"]["pinned_message_expires_at"], int)
    assert "timestamp" not in messages[1]


def test_get_chat_by_channel_dedups_current_pin_against_live_pin_event() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
        ]
    )
    live_pin = {
        "duration": "1200",
        "message": {
            "content": "Existing pin",
            "id": "startup-pinned-message",
            "sender": {"id": 400, "username": "PinnedAuthor"},
        },
        "pinnedBy": {"id": 500, "username": "StartupModerator"},
    }
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages", "pins"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(PINNED_MESSAGE_CREATED_EVENT, live_pin)]]
            ),
        )

        messages = list(chat.chat)

    assert [message["message_type"] for message in messages] == [
        "text_message",
        "pinned_message",
    ]


def test_get_chat_by_channel_dedups_live_against_preloaded() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )
    duplicate = {"id": "preloaded-1", "content": "dup", "type": "message"}
    fresh = {"id": "fresh", "content": "new", "type": "message"}
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [
                        pusher_frame(CHAT_MESSAGE_EVENT, duplicate),
                        pusher_frame(CHAT_MESSAGE_EVENT, fresh),
                    ]
                ]
            ),
        )
        ids = [m["message_id"] for m in chat.chat]
        assert ids == ["preloaded-1", "preloaded-2", "fresh"]


def test_get_chat_by_channel_filters_by_message_type() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    live_data = load_fixture("chat_message_event_data.json")
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_types": ["subscription"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]]
            ),
        )
        # text_message is filtered out; nothing should be emitted.
        assert list(chat.chat) == []


def test_get_chat_by_channel_reconnects_on_disconnect() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    frame_one = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "a", "content": "1"})
    frame_two = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "b", "content": "2"})
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=factory,
            frame_iterator=make_frame_iterator(
                [[frame_one, ConnectionError("drop")], [frame_two]]
            ),
        )
        ids = [m["message_id"] for m in chat.chat]
        assert ids == ["a", "b"]
    assert len(created) == 2  # reconnected once
    assert created[0].close_count >= 1


def test_get_chat_by_channel_reports_live_diagnostics() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frames = [
        {"event": "pusher:connection_established", "data": "{}"},
        {"event": "App\\Events\\FutureEvent", "data": "{}"},
        {"event": CHAT_MESSAGE_EVENT, "data": "not JSON"},
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": "a", "type": "message", "content": "1"},
        ),
    ]
    final_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "b", "type": "message", "content": "2"},
    )

    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[*frames, ConnectionError("drop")], [final_frame]]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == ["a", "b"]

    assert isinstance(chat.diagnostics["last_websocket_frame_timestamp"], int)
    diagnostics_without_timestamp = {
        **chat.diagnostics,
        "last_websocket_frame_timestamp": None,
    }
    assert diagnostics_without_timestamp == {
        "websocket_frame_count": 5,
        "control_frame_count": 1,
        "parsed_event_count": 2,
        "unsupported_event_count": 1,
        "unknown_message_type_count": 0,
        "malformed_event_count": 1,
        "invalid_websocket_frame_count": 0,
        "websocket_reconnect_count": 1,
        "pusher_error_count": 0,
        "pusher_key_recovery_count": 0,
        "last_websocket_frame_timestamp": None,
    }


def test_get_chat_by_channel_adds_distinct_receive_timestamp_fallback() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    missing_timestamp = pusher_frame(
        MESSAGE_DELETED_EVENT,
        {"id": "missing", "message": {"id": "deleted"}},
    )
    provider_timestamp = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "provider",
            "type": "message",
            "content": "provider time",
            "created_at": "2025-06-14T12:00:00Z",
        },
    )

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service.time,
            "time_ns",
            side_effect=[1_700_000_000_000_000_000, 1_800_000_000_000_000_000],
        ),
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[missing_timestamp, provider_timestamp]]
            ),
        )
        messages = list(chat.chat)

    assert messages[0]["received_timestamp"] == 1_700_000_000_000_000
    assert "timestamp" not in messages[0]
    assert ItemFormatter().format(messages[0], format_name="kick") == (
        "2023-11-14 22:13:20 [received] | [Message deleted: deleted]"
    )
    assert messages[1]["timestamp"] == 1_749_902_400_000_000
    assert "received_timestamp" not in messages[1]


def test_get_chat_by_channel_rediscovers_key_after_pusher_error() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    live_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "after-refresh", "content": "restored"},
    )
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=factory,
            frame_iterator=make_frame_iterator(
                [
                    [pusher_frame(PUSHER_ERROR, {"message": "stale key"})],
                    [live_frame],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == ["after-refresh"]

    assert len(created) == 2
    assert created[0].force_discover is False
    assert created[1].force_discover is True


def test_get_chat_by_channel_repeated_pusher_error_is_terminal() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    error_frame = pusher_frame(PUSHER_ERROR, {"message": "rejected"})

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        pytest.raises(KickError, match="protocol failure"),
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([[error_frame], [error_frame]]),
        )
        list(chat.chat)


def test_get_chat_by_channel_backfills_messages_missed_during_reconnect() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )

    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([[ConnectionError("drop")], []]),
        )

        assert [message["message_id"] for message in chat.chat] == [
            "preloaded-1",
            "preloaded-2",
        ]


def test_get_chat_by_channel_repeated_disconnects_exhaust_budget() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        pytest.raises(RetriesExceeded),
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"max_attempts": 2},
            transport_factory=factory,
            frame_iterator=make_frame_iterator(
                [
                    [ConnectionError("drop one")],
                    [ConnectionError("drop two")],
                ]
            ),
        )
        list(chat.chat)

    assert len(created) == 2
    assert all(transport.close_count >= 1 for transport in created)


def test_get_chat_by_channel_offline_succeeds_with_offline_title() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(200, load_fixture("channel_offline.json"))])
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = live_service.get_chat_by_channel(
            downloader, "examplechannel", _request()
        )
        assert chat.title == "examplechannel"
        assert chat.status == "idle"


@pytest.mark.parametrize(
    "bounds",
    [
        {"start_time": 10},
        {"end_time": "00:00:20"},
    ],
)
def test_get_chat_by_channel_rejects_replay_time_bounds(bounds: dict[str, Any]) -> None:
    with pytest.raises(InvalidParameter, match="Kick live chat does not support"):
        live_service.get_chat_by_channel(
            FakeDownloader(),
            "examplechannel",
            _request(**bounds),
        )


@pytest.mark.parametrize(
    "error",
    [
        KickError("terminal"),
        CaptchaChallengeRequired("challenge"),
        OSError("offline"),
    ],
)
def test_preloaded_history_is_best_effort(error: Exception) -> None:
    class FailingClient:
        @staticmethod
        def fetch_preloaded_chat_state(_channel_id: str, _username: str) -> Any:
            raise error

    downloader = MagicMock()
    downloader._kick_client = FailingClient()

    assert (
        list(
            live_service._iter_preloaded_chat(
                downloader,
                "123",
                "creator",
                lambda _message: True,
            )
        )
        == []
    )


def test_preloaded_history_does_not_swallow_keyboard_interrupt() -> None:
    class InterruptingClient:
        @staticmethod
        def fetch_preloaded_chat_state(_channel_id: str, _username: str) -> Any:
            raise KeyboardInterrupt

    downloader = MagicMock()
    downloader._kick_client = InterruptingClient()

    with pytest.raises(KeyboardInterrupt):
        list(
            live_service._iter_preloaded_chat(
                downloader,
                "123",
                "creator",
                lambda _message: True,
            )
        )


def test_preloaded_chat_captures_and_skips_malformed_current_pin(
    caplog: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedPinClient:
        @staticmethod
        def fetch_preloaded_chat_state(
            _channel_id: str, _username: str
        ) -> PreloadedChatState:
            return PreloadedChatState(messages=[], pinned_message={"duration": 1})

    downloader = MagicMock()
    downloader._kick_client = MalformedPinClient()
    caplog.set_level("DEBUG", logger=live_service.logger.name)
    captured = []
    monkeypatch.setattr(
        live_service,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    messages = list(
        live_service._iter_preloaded_chat(
            downloader,
            "123",
            "creator",
            lambda _message: True,
        )
    )

    assert messages == []
    assert "Skipping malformed Kick startup pin" in caplog.text
    assert captured[0][0][0] == "kick-malformed-preloaded-pin"
    assert captured[0][0][1]["raw"] == {"duration": 1}
    assert captured[0][1]["sample_limit"] == 10

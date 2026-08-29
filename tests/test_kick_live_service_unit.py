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
    PINNED_MESSAGE_DELETED_EVENT,
    POLL_DELETE_EVENT,
    POLL_UPDATE_EVENT,
    PUSHER_ERROR,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
    SUBSCRIPTION_EVENT,
)
from chat_downloader.sites.kick.errors import (
    KickError,
    KickForwardHistoryRejected,
    KickServerError,
)
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


@pytest.mark.parametrize(
    ("checkpoint", "expected"),
    [
        (None, 10_000_000),
        (5_000_000, 10_000_000),
        (15_000_000, 15_000_000),
        (20_000_000, 10_000_000),
        (21_000_000, 10_000_000),
    ],
)
def test_bounded_reconnect_start_limits_and_validates_checkpoint(
    checkpoint: int | None,
    expected: int,
) -> None:
    assert live_service._bounded_reconnect_start(checkpoint, 20_000_000) == expected


def test_newest_provider_timestamp_ignores_invalid_and_regressive_values() -> None:
    assert live_service._newest_provider_timestamp(None, {}) is None
    assert live_service._newest_provider_timestamp(12, {"timestamp": True}) == 12
    assert live_service._newest_provider_timestamp(12, {"timestamp": 10}) == 12
    assert live_service._newest_provider_timestamp(None, {"timestamp": 15}) == 15
    assert live_service._timestamp_sort_key({}) == -1
    assert live_service._timestamp_sort_key({"timestamp": True}) == -1
    assert live_service._timestamp_sort_key({"timestamp": 15}) == 15


def test_reconnect_backfill_time_filters_preloaded_fallback_and_keeps_pin() -> None:
    client = MagicMock()
    client.fetch_message_page.side_effect = KickForwardHistoryRejected("unsupported")
    pinned_payload = load_fixture("preloaded_messages_with_pin.json")["data"]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            {
                "id": "after",
                "content": "too new",
                "created_at": "2026-01-01T00:00:21Z",
                "type": "message",
            },
            {
                "id": "inside",
                "content": "recover",
                "created_at": "2026-01-01T00:00:15Z",
                "type": "message",
            },
            {
                "id": "before",
                "content": "too old",
                "created_at": "2026-01-01T00:00:09Z",
                "type": "message",
            },
        ],
        pinned_message=pinned_payload["pinned_message"],
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )

    assert [message["message_id"] for message in messages] == [
        "inside",
        "kick-pin:startup-pinned-message",
    ]


def test_reconnect_backfill_keeps_history_when_pin_refresh_fails() -> None:
    client = MagicMock()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [
                {
                    "id": "inside",
                    "content": "recover",
                    "created_at": "2026-01-01T00:00:15Z",
                    "type": "message",
                }
            ],
            "cursor": None,
        }
    }
    client.fetch_preloaded_chat_state.side_effect = OSError("pin unavailable")
    downloader = MagicMock()
    downloader._kick_client = client

    messages = list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )

    assert [message["message_id"] for message in messages] == ["inside"]


def test_reconnect_backfill_keeps_earlier_pages_after_later_failure() -> None:
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        {
            "data": {
                "messages": [
                    {
                        "id": "page-one",
                        "content": "preferred forward copy",
                        "created_at": "2026-01-01T00:00:12Z",
                        "type": "message",
                    }
                ],
                "cursor": "1767225612000000",
            }
        },
        KickServerError("later page failed"),
    ]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            {
                "id": "fallback-new",
                "content": "fallback recovery",
                "created_at": "2026-01-01T00:00:14Z",
                "type": "message",
            },
            {
                "id": "page-one",
                "content": "overlapping fallback copy",
                "created_at": "2026-01-01T00:00:12Z",
                "type": "message",
            },
        ],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )

    assert [message["message_id"] for message in messages] == [
        "page-one",
        "fallback-new",
    ]
    assert messages[0]["message"] == "preferred forward copy"
    assert client.fetch_message_page.call_count == 2


def test_reconnect_backfill_reconciles_preload_after_empty_forward_page() -> None:
    client = MagicMock()
    client.fetch_message_page.return_value = {"data": {"messages": [], "cursor": None}}
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            {
                "id": "preload-only",
                "content": "late preload copy",
                "created_at": "2026-01-01T00:00:15Z",
                "type": "message",
            }
        ],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )

    assert [message["message_id"] for message in messages] == ["preload-only"]


def test_reconnect_backfill_closes_history_at_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator_closed = False

    def iter_history(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal generator_closed
        try:
            for index in range(5):
                yield {
                    "id": f"message-{index}",
                    "content": "bounded",
                    "created_at": f"2026-01-01T00:00:1{index}Z",
                    "type": "message",
                }
        finally:
            generator_closed = True

    monkeypatch.setattr(live_service, "_RECONNECT_BACKFILL_RECORD_LIMIT", 3)
    monkeypatch.setattr(live_service, "iter_forward_history", iter_history)
    client = MagicMock()
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )

    assert [message["message_id"] for message in messages] == [
        "message-0",
        "message-1",
        "message-2",
    ]
    assert generator_closed is True


def test_reconnect_backfill_caps_pages_without_usable_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_service, "_RECONNECT_BACKFILL_PAGE_LIMIT", 2)
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        {
            "data": {
                "messages": [
                    {
                        "id": "too-old-one",
                        "content": "ignored",
                        "created_at": "2000-01-01T00:00:00Z",
                        "type": "message",
                    }
                ],
                "cursor": "1767225611000000",
            }
        },
        {
            "data": {
                "messages": [
                    {
                        "id": "too-old-two",
                        "content": "ignored",
                        "created_at": "2000-01-01T00:00:01Z",
                        "type": "message",
                    }
                ],
                "cursor": "1767225612000000",
            }
        },
        AssertionError("reconnect page limit was not enforced"),
    ]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    assert (
        list(
            live_service._iter_reconnect_backfill(
                downloader,
                "123",
                "creator",
                None,
                1_767_225_620_000_000,
                _request(max_attempts=1),
            )
        )
        == []
    )
    assert client.fetch_message_page.call_count == 2


def test_reconnect_backfill_caps_raw_records_before_history_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_service, "_RECONNECT_BACKFILL_RECORD_LIMIT", 3)
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        {
            "data": {
                "messages": [
                    {
                        "id": f"too-old-{index}",
                        "content": "ignored",
                        "created_at": f"2000-01-01T00:00:0{index}Z",
                        "type": "message",
                    }
                    for index in range(5)
                ],
                "cursor": "1767225611000000",
            }
        },
        AssertionError("reconnect raw-record limit was not enforced"),
    ]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    assert (
        list(
            live_service._iter_reconnect_backfill(
                downloader,
                "123",
                "creator",
                None,
                1_767_225_620_000_000,
                _request(max_attempts=1),
            )
        )
        == []
    )
    assert client.fetch_message_page.call_count == 1


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


def test_get_chat_by_channel_preserves_live_celebration() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    celebration = load_fixture("celebration_message_event_data.json")
    with patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(CHAT_MESSAGE_EVENT, celebration)]]
            ),
        )
        messages = list(chat.chat)

    assert messages == [
        {
            "message_id": "celebration-live-1",
            "message_type": "text_message",
            "message": "Celebrating 20 months!",
            "timestamp": 1787968059000000,
            "author": {
                "id": "88",
                "display_name": "RenewalUser",
                "name": "renewal-user",
                "colour": "#72ACED",
                "badges": [{"name": "subscriber", "title": "Subscriber", "count": 20}],
            },
            "metadata": {
                "celebration": {
                    "id": "celebration-renewal-1",
                    "type": "subscription_renewed",
                    "total_months": 20,
                    "created_at": 1787880598835777,
                }
            },
        }
    ]
    assert chat.diagnostics["unknown_message_type_count"] == 0


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
        "malformed_event_type_counts": {"text_message": 1},
        "invalid_websocket_frame_count": 0,
        "websocket_reconnect_count": 1,
        "pusher_error_count": 0,
        "pusher_key_recovery_count": 0,
        "last_websocket_frame_timestamp": None,
    }


def test_get_chat_by_channel_emits_compact_live_events() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frames = [
        pusher_frame(
            SUBSCRIPTION_EVENT,
            load_fixture("subscription_event_compact.json"),
        ),
        pusher_frame(
            PINNED_MESSAGE_DELETED_EVENT,
            load_fixture("pinned_message_deleted_event_empty.json"),
        ),
    ]

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 11_000]),
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([frames]),
        )
        messages = list(chat.chat)

    assert messages == [
        {
            "message_id": "kick-subscription:11",
            "message_type": "subscription",
            "message": "",
            "received_timestamp": 11,
            "author": {
                "display_name": "compactsubscriber",
                "name": "compactsubscriber",
            },
            "metadata": {"months": 1},
        },
        {
            "message_id": "kick-unpin:12",
            "message_type": "pinned_message_deleted",
            "message": "",
            "received_timestamp": 12,
        },
    ]
    formatter = ItemFormatter()
    assert formatter.format(messages[0], format_name="kick") == (
        "1970-01-01 00:00:00 [received] | [Subscription] compactsubscriber"
    )
    assert formatter.format(messages[1], format_name="kick") == (
        "1970-01-01 00:00:00 [received] | [Pinned message removed]"
    )


def test_get_chat_by_channel_emits_poll_state_events() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frames = [
        pusher_frame(POLL_UPDATE_EVENT, load_fixture("poll_update_event.json")),
        pusher_frame(POLL_DELETE_EVENT, load_fixture("poll_deleted_event.json")),
    ]

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 12_000]),
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["polls"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([frames]),
        )
        messages = list(chat.chat)

    assert [message["message_type"] for message in messages] == [
        "poll_update",
        "poll_deleted",
    ]
    assert messages[0]["message_id"] == "kick-poll-update:11"
    assert messages[0]["received_timestamp"] == 11
    assert messages[0]["metadata"]["options"][1]["label"] == "Option B"
    assert messages[1] == {
        "message_id": "kick-poll-deleted:12",
        "message_type": "poll_deleted",
        "message": "",
        "received_timestamp": 12,
    }
    formatter = ItemFormatter()
    assert formatter.format(messages[0], format_name="kick") == (
        "1970-01-01 00:00:00 [received] | [Poll update] Example poll"
    )
    assert formatter.format(messages[1], format_name="kick") == (
        "1970-01-01 00:00:00 [received] | [Poll deleted]"
    )
    assert chat.diagnostics["unsupported_event_count"] == 0


def test_get_chat_by_channel_messages_filter_excludes_poll_events() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frames = [
        pusher_frame(POLL_UPDATE_EVENT, load_fixture("poll_update_event.json")),
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": "visible", "type": "message", "content": "Visible"},
        ),
    ]

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 12_000]),
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([frames]),
        )
        messages = list(chat.chat)

    assert [message["message_id"] for message in messages] == ["visible"]
    assert chat.diagnostics["parsed_event_count"] == 2
    assert chat.diagnostics["parsed_event_count"] == 2
    assert chat.diagnostics["malformed_event_count"] == 0
    assert chat.diagnostics["malformed_event_type_counts"] == {}


def test_live_diagnostics_make_receive_timestamps_strictly_monotonic() -> None:
    diagnostics = live_service._KickLiveDiagnostics()

    with patch.object(
        live_service.time,
        "time_ns",
        side_effect=[11_000, 11_000, 10_000],
    ):
        timestamps = [diagnostics.record_frame() for _ in range(3)]

    assert timestamps == [11, 12, 13]
    assert diagnostics.summary["websocket_frame_count"] == 3
    assert diagnostics.summary["last_websocket_frame_timestamp"] == 13


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
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "during-refresh",
                                "content": "recovered",
                                "created_at": "2026-01-01T00:00:08Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
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
    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service.time,
            "time_ns",
            side_effect=[
                1_767_225_605_500_000_000,
                1_767_225_612_000_000_000,
                1_767_225_613_500_000_000,
            ],
        ),
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
        assert [message["message_id"] for message in chat.chat] == [
            "during-refresh",
            "after-refresh",
        ]

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


@pytest.mark.parametrize(
    "recovery_frame",
    [
        ConnectionError("drop"),
        pusher_frame(PUSHER_ERROR, {"message": "stale key"}),
    ],
    ids=["disconnect", "pusher-error"],
)
def test_reconnect_backfill_waits_for_subscription_confirmation(
    recovery_frame: object,
) -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "missed",
                                "content": "confirmed recovery",
                                "created_at": "2026-01-01T00:00:15Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    iterator_call = 0

    def frame_iterator(_transport: FakeTransport) -> Any:
        nonlocal iterator_call
        iterator_call += 1
        if iterator_call == 1:
            if isinstance(recovery_frame, Exception):
                raise recovery_frame
            yield recovery_frame
            return
        assert len(session.calls) == 2
        yield {"event": "pusher:connection_established", "data": "{}"}
        assert len(session.calls) == 2
        yield pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})
        assert len(session.calls) == 4

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service.time,
            "time_ns",
            return_value=1_767_225_620_000_000_000,
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=frame_iterator,
        )
        assert [message["message_id"] for message in chat.chat] == ["missed"]


def test_get_chat_by_channel_backfills_messages_missed_during_reconnect() -> None:
    downloader = FakeDownloader()
    forward_page = {
        "data": {
            "messages": [
                {
                    "id": "a",
                    "content": "duplicate",
                    "created_at": "2026-01-01T00:00:05Z",
                    "type": "message",
                },
                {
                    "id": "missed",
                    "content": "recovered",
                    "created_at": "2026-01-01T00:00:08Z",
                    "type": "message",
                },
            ],
            "cursor": None,
        }
    }
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, forward_page),
            FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
        ]
    )
    frame_one = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "a",
            "content": "before outage",
            "created_at": "2026-01-01T00:00:05Z",
            "type": "message",
        },
    )
    frame_two = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "b",
            "content": "after outage",
            "created_at": "2026-01-01T00:00:13Z",
            "type": "message",
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
            side_effect=[
                1_767_225_605_500_000_000,
                1_767_225_612_000_000_000,
                1_767_225_613_500_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages", "pins"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[frame_one, ConnectionError("drop")], [frame_two]]
            ),
        )

        assert [message["message_id"] for message in chat.chat] == [
            "a",
            "missed",
            "kick-pin:startup-pinned-message",
            "b",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.000000Z"
    }
    assert session.calls[3][1]["params"] is None


def test_get_chat_by_channel_aligns_bounded_backfill_to_modest_provider_skew() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "late-lower-timestamp",
                                "content": "preserved despite skew",
                                "created_at": "2026-01-01T00:00:20.500000Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    skewed_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "before-outage",
            "content": "provider clock is ahead",
            "created_at": "2026-01-01T00:00:21Z",
            "type": "message",
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
            side_effect=[
                1_767_225_619_000_000_000,
                1_767_225_620_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [skewed_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == [
            "before-outage",
            "late-lower-timestamp",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:10.000000Z"
    }


def test_get_chat_by_channel_aligns_backfill_to_negative_provider_skew() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "missed",
                                "content": "provider clock trails client",
                                "created_at": "2026-01-01T00:00:10Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    lagging_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "before-outage",
            "content": "provider clock trails client",
            "created_at": "2026-01-01T00:00:05Z",
            "type": "message",
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
            side_effect=[
                1_767_225_610_000_000_000,
                1_767_225_620_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [lagging_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == [
            "before-outage",
            "missed",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.000000Z"
    }


def test_negative_timestamp_delta_cannot_cut_off_near_confirmation_message() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "near-confirmation",
                                "content": "must survive delivery latency",
                                "created_at": "2026-01-01T00:00:11.800000Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    delayed_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "before-outage",
            "content": "ordinary delivery delay",
            "created_at": "2026-01-01T00:00:05Z",
            "type": "message",
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
            side_effect=[
                1_767_225_605_500_000_000,
                1_767_225_612_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [delayed_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == [
            "before-outage",
            "near-confirmation",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.000000Z"
    }


def test_current_provider_clock_sample_replaces_stale_positive_skew() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "missed",
                                "content": "inside the current clock window",
                                "created_at": "2026-01-01T00:00:18.500000Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    formerly_skewed_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "skewed",
            "content": "old positive skew",
            "created_at": "2026-01-01T00:00:18Z",
            "type": "message",
        },
    )
    aligned_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "aligned",
            "content": "current clocks align",
            "created_at": "2026-01-01T00:00:18Z",
            "type": "message",
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
            side_effect=[
                1_767_225_609_000_000_000,
                1_767_225_618_000_000_000,
                1_767_225_620_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [formerly_skewed_frame, aligned_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == [
            "skewed",
            "aligned",
            "missed",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:18.000000Z"
    }


def test_extreme_provider_timestamp_cannot_poison_reconnect_window() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "missed",
                                "content": "still in local window",
                                "created_at": "2026-01-01T00:00:15Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    poisoned_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "bad-clock",
            "content": "invalid future provider time",
            "created_at": "2036-01-01T00:00:00Z",
            "type": "message",
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
            side_effect=[
                1_767_225_619_000_000_000,
                1_767_225_620_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [poisoned_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert [message["message_id"] for message in chat.chat] == [
            "bad-clock",
            "missed",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:10.000000Z"
    }


def test_get_chat_by_channel_caps_reconnect_backfill_at_ten_seconds() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(
                200,
                {
                    "data": {
                        "messages": [
                            {
                                "id": "missed",
                                "content": "bounded recovery",
                                "created_at": "2026-01-01T00:00:11Z",
                                "type": "message",
                            }
                        ],
                        "cursor": None,
                    }
                },
            ),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service.time,
            "time_ns",
            return_value=1_767_225_620_000_000_000,
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )

        assert [message["message_id"] for message in chat.chat] == ["missed"]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:10.000000Z"
    }


def test_get_chat_by_channel_uses_receive_time_without_provider_timestamp() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": [], "cursor": None}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "no-provider-time", "content": "checkpoint from receive time"},
    )

    with (
        patch(
            "chat_downloader.sites.kick.api_client.create_kick_session",
            return_value=session,
        ),
        patch.object(
            live_service.time,
            "time_ns",
            side_effect=[
                1_767_225_605_500_000_000,
                1_767_225_612_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )

        assert [message["message_id"] for message in chat.chat] == ["no-provider-time"]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.500000Z"
    }


def test_filtered_message_still_advances_reconnect_checkpoint() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": [], "cursor": None}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    filtered_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "filtered",
            "content": "checkpoint only",
            "created_at": "2026-01-01T00:00:05Z",
            "type": "message",
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
            side_effect=[
                1_767_225_605_500_000_000,
                1_767_225_612_000_000_000,
            ],
        ),
    ):
        chat = _build_chat(
            FakeDownloader(),
            request_kwargs={"message_types": ["subscription"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [filtered_frame, ConnectionError("drop")],
                    [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
                ]
            ),
        )
        assert list(chat.chat) == []

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.000000Z"
    }


def test_get_chat_by_channel_repeated_disconnects_exhaust_budget() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
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
    assert "Skipping malformed Kick current pin" in caplog.text
    assert captured[0][0][0] == "kick-malformed-preloaded-pin"
    assert captured[0][0][1]["raw"] == {"duration": 1}
    assert captured[0][1]["sample_limit"] == 10

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
    STREAM_HOST_EVENT,
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


class _WindowSession(FakeKickSession):
    """Serve subsequent empty five-second windows without consuming pin refresh."""

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        is_window = bool((kwargs.get("params") or {}).get("start_time"))
        if is_window and getattr(self, "_previous_window", False):
            self.calls.append((url, kwargs))
            self.requested_urls.append(url)
            return _empty_response()
        result = super().get(url, **kwargs)
        self._previous_window = is_window
        return result


def _request(**overrides: Any) -> ChatRequest:
    params: dict[str, Any] = {
        "url": "https://kick.com/examplechannel",
        "max_attempts": 2,
        "retry_timeout": 0,
        "interruptible_retry": False,
    }
    params.update(overrides)
    return ChatRequest.from_kwargs(**params)


def _session_patch(session):
    return patch(
        "chat_downloader.sites.kick.api_client.create_kick_session",
        return_value=session,
    )


def _live_session(*responses):
    return _WindowSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            *responses,
        ]
    )


def _backfill(downloader):
    return list(
        live_service._iter_reconnect_backfill(
            downloader,
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )


def _empty_response():
    return FakeResponse(200, {"data": {"messages": []}})


def _message(message_id, content, created_at):
    return {
        "id": message_id,
        "content": content,
        "created_at": created_at,
        "type": "message",
    }


def _page(messages, cursor):
    return {"data": {"messages": messages, "cursor": cursor}}


class _NoRetryDownloader(FakeDownloader):
    """Downloader whose ``retry`` never sleeps or raises (drives exhaustion)."""

    @staticmethod
    def retry(*_args: Any, **_kwargs: Any) -> None:
        return None


@pytest.mark.parametrize(
    ("proxies", "expected"),
    [
        ({"https": "http://proxy.example:8080"}, "http://proxy.example:8080"),
        ({}, None),
        ({"https": ""}, None),
    ],
)
def test_resolve_ws_proxy(proxies, expected) -> None:
    downloader = MagicMock()
    downloader.session.proxies = proxies
    downloader.session.trust_env = False
    assert live_service._resolve_ws_proxy(downloader) == expected


def test_resolve_ws_proxy_returns_none_without_session() -> None:
    assert live_service._resolve_ws_proxy(object()) is None


# ── _resolve_channel ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "title"),
    [
        ("channel_live.json", "Example live stream title"),
        ("channel_offline.json", "examplechannel"),
    ],
)
def test_resolve_channel(fixture, title) -> None:
    assert live_service._resolve_channel(load_fixture(fixture), "examplechannel") == (
        "12345",
        "54321",
        title,
    )


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


def test_resolve_channel_live_without_title_falls_back_to_username() -> None:
    data = {"id": 1, "chatroom": {"id": 2}, "livestream": {}}
    _cid, _rid, title = live_service._resolve_channel(data, "fallbackname")
    assert title == "fallbackname"


# ── _fetch_channel_with_retry ─────────────────────────────────────────────────


def test_fetch_channel_with_retry_succeeds_after_transient() -> None:
    payload = load_fixture("channel_live.json")
    downloader = FakeDownloader()
    session = _WindowSession([FakeResponse(503, {"e": 1}), FakeResponse(200, payload)])
    with _session_patch(session):
        data = live_service._fetch_channel_with_retry(
            downloader, "examplechannel", _request()
        )
    assert data["id"] == 12345


@pytest.mark.parametrize(
    ("downloader_type", "error", "match"),
    [
        (FakeDownloader, RetriesExceeded, None),
        (_NoRetryDownloader, RuntimeError, "unreachable"),
    ],
)
def test_fetch_channel_with_retry_exhausts(downloader_type, error, match) -> None:
    session = _WindowSession([FakeResponse(500, {"e": 1})])
    with _session_patch(session), pytest.raises(error, match=match):
        live_service._fetch_channel_with_retry(
            downloader_type(), "x", _request(max_attempts=1)
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


@pytest.mark.parametrize(("requested", "effective"), [(0.1, 1.0), (2.5, 2.5)])
def test_open_subscribed_transport_separates_connect_and_receive_timeouts(
    requested,
    effective,
) -> None:
    transport = FakeTransport()
    downloader = FakeDownloader(connect_timeout=7.5, read_timeout=22.0)
    opened = live_service._open_subscribed_transport(
        downloader,
        "54321",
        _request(message_receive_timeout=requested),
        lambda: transport,
    )
    assert opened is transport
    assert transport.connect_timeout == pytest.approx(7.5)
    assert transport.receive_timeout == pytest.approx(effective)
    assert transport.subscribed_to == "54321"


@pytest.mark.parametrize(
    ("downloader_type", "error", "match"),
    [
        (_NoRetryDownloader, RuntimeError, "unreachable"),
        (
            FakeDownloader,
            RetriesExceeded,
            "Last Kick WebSocket error: fake connect failure",
        ),
    ],
)
def test_open_subscribed_transport_terminal_error(
    downloader_type, error, match
) -> None:
    with pytest.raises(error, match=match):
        live_service._open_subscribed_transport(
            downloader_type(),
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
            _message("after", "too new", "2026-01-01T00:00:21Z"),
            _message("inside", "recover", "2026-01-01T00:00:15Z"),
            _message("before", "too old", "2026-01-01T00:00:09Z"),
        ],
        pinned_message=pinned_payload["pinned_message"],
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = _backfill(downloader)

    assert [message["message_id"] for message in messages] == [
        "inside",
        "kick-pin:startup-pinned-message",
    ]


def test_reconnect_backfill_keeps_history_when_pin_refresh_fails() -> None:
    client = MagicMock()
    client.fetch_message_page.return_value = _page(
        [_message("inside", "recover", "2026-01-01T00:00:15Z")], None
    )
    client.fetch_preloaded_chat_state.side_effect = OSError("pin unavailable")
    downloader = MagicMock()
    downloader._kick_client = client

    messages = _backfill(downloader)

    assert [message["message_id"] for message in messages] == ["inside"]


def test_reconnect_backfill_keeps_earlier_pages_after_later_failure() -> None:
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        _page(
            [_message("page-one", "preferred forward copy", "2026-01-01T00:00:12Z")],
            "1767225612000000",
        ),
        KickServerError("later page failed"),
    ]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            _message("fallback-new", "fallback recovery", "2026-01-01T00:00:14Z"),
            _message("page-one", "overlapping fallback copy", "2026-01-01T00:00:12Z"),
        ],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = _backfill(downloader)

    assert [message["message_id"] for message in messages] == [
        "page-one",
        "fallback-new",
    ]
    assert messages[0]["message"] == "preferred forward copy"
    assert client.fetch_message_page.call_count == 2


def test_reconnect_backfill_reconciles_preload_after_empty_forward_page() -> None:
    client = MagicMock()
    client.fetch_message_page.return_value = _page([], None)
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            _message("preload-only", "late preload copy", "2026-01-01T00:00:15Z")
        ],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client

    messages = _backfill(downloader)

    assert [message["message_id"] for message in messages] == ["preload-only"]


def test_reconnect_backfill_closes_history_at_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator_closed = False

    def iter_history(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal generator_closed
        try:
            for index in range(5):
                yield _message(
                    f"message-{index}", "bounded", f"2026-01-01T00:00:1{index}Z"
                )
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

    messages = _backfill(downloader)

    assert [message["message_id"] for message in messages] == [
        "message-0",
        "message-1",
        "message-2",
    ]
    assert generator_closed is True


@pytest.mark.parametrize(
    ("limit_name", "limit", "page_sizes"),
    [("PAGE", 2, [1, 1]), ("RECORD", 3, [5])],
)
def test_reconnect_backfill_caps_unusable_history(
    monkeypatch: pytest.MonkeyPatch,
    limit_name,
    limit,
    page_sizes,
) -> None:
    monkeypatch.setattr(live_service, f"_RECONNECT_BACKFILL_{limit_name}_LIMIT", limit)
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        _page(
            [
                _message(
                    f"old-{page}-{index}", "ignored", f"2000-01-01T00:00:0{index}Z"
                )
                for index in range(size)
            ],
            str(1767225611000000 + page * 1000000),
        )
        for page, size in enumerate(page_sizes)
    ] + [AssertionError("reconnect history limit was not enforced")]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[],
        pinned_message=None,
    )
    downloader = MagicMock()
    downloader._kick_client = client
    assert _backfill(downloader) == []
    assert client.fetch_message_page.call_count == len(page_sizes)


# ── end-to-end via get_chat_by_channel ────────────────────────────────────────


def _build_chat(downloader: FakeDownloader, **kwargs: Any) -> Any:
    return live_service.get_chat_by_channel(
        downloader,
        "examplechannel",
        _request(**kwargs.pop("request_kwargs", {})),
        **kwargs,
    )


def _fake_chat(downloader, batches, **kwargs):
    return _build_chat(
        downloader,
        transport_factory=FakeTransport,
        frame_iterator=make_frame_iterator(batches),
        **kwargs,
    )


def test_get_chat_by_channel_emits_preloaded_then_live() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        FakeResponse(200, load_fixture("preloaded_messages.json")),
    )
    live_data = load_fixture("chat_message_event_data.json")
    with _session_patch(session):
        chat = _fake_chat(downloader, [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]])
        assert chat.title == "Example live stream title"
        assert chat.status == "live"
        messages = list(chat.chat)
        ids = [m["message_id"] for m in messages]
        assert ids == ["preloaded-1", "preloaded-2", "live-1"]
        assert chat.diagnostics["preloaded_emitted_count"] == 2
        assert chat.diagnostics["live_emitted_count"] == 1
        assert chat.diagnostics["reconnect_backfill_emitted_count"] == 0


def test_get_chat_by_channel_preserves_live_celebration() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        _empty_response(),
    )
    celebration = load_fixture("celebration_message_event_data.json")
    with _session_patch(session):
        chat = _fake_chat(downloader, [[pusher_frame(CHAT_MESSAGE_EVENT, celebration)]])
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
    session = _live_session(
        _empty_response(),
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
        _session_patch(session),
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
    session = _live_session(
        _empty_response(),
    )
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "live", "content": "message"},
    )

    with _session_patch(session):
        chat = _fake_chat(FakeDownloader(), [[frame]])
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
    session = _live_session(
        _empty_response(),
        _empty_response(),
        _empty_response(),
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

    with _session_patch(session):
        chat = _fake_chat(
            FakeDownloader(),
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
            ],
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
        ((f"kick-websocket-frame-{kind}", frame), {"sample_limit": 3})
        for kind, frames in [
            ("text-message", message_frames[:2]),
            ("subscription", subscription_frames[:2]),
            ("text-message", message_frames[2:3]),
            ("subscription", subscription_frames[2:3]),
        ]
        for frame in frames
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
    session = _live_session(
        _empty_response(),
    )
    message_frames = [
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {
                "id": f"msg-{index}",
                "type": "reply",
                "content": "[emote:123:hello]",
                "metadata": {"original_sender": {"username": "Parent"}},
                "sender": {
                    "identity": {"badges_v2": [{"name": "level", "selected": True}]}
                },
            },
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

    with _session_patch(session):
        chat = _fake_chat(FakeDownloader(), [[*message_frames, *subscription_frames]])
        assert len(list(chat.chat)) == 8

    assert len(list(sample_dir.glob("kick-websocket-frame-text-message-*.json"))) == 3
    assert len(list(sample_dir.glob("kick-websocket-frame-subscription-*.json"))) == 3

    for shape in ("in-reply-to", "emotes", "badges"):
        assert (
            len(
                list(sample_dir.glob(f"kick-websocket-frame-text-shape-{shape}-*.json"))
            )
            == 3
        )
    assert len(list(sample_dir.glob("*.json"))) == 15


@pytest.mark.parametrize("duplicate_pin", [False, True])
def test_get_chat_by_channel_emits_current_pin_once(duplicate_pin) -> None:
    downloader = FakeDownloader()
    session = _live_session(
        FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
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
    frames = (
        [pusher_frame(PINNED_MESSAGE_CREATED_EVENT, live_pin)] if duplicate_pin else []
    )
    with _session_patch(session):
        chat = _fake_chat(
            downloader,
            [frames],
            request_kwargs={"message_groups": ["messages", "pins"]},
        )

        messages = list(chat.chat)

    assert [message["message_type"] for message in messages] == [
        "text_message",
        "pinned_message",
    ]
    assert chat.diagnostics["preloaded_emitted_count"] == 2
    assert chat.diagnostics["live_emitted_count"] == 0
    assert messages[1]["message_id"] == "kick-pin:startup-pinned-message"
    assert messages[1]["metadata"]["pinned_by"]["display_name"] == ("StartupModerator")
    assert isinstance(
        messages[1]["metadata"]["original_message_created_at"],
        int,
    )
    assert isinstance(messages[1]["metadata"]["pinned_message_expires_at"], int)
    assert "timestamp" not in messages[1]


def test_get_chat_by_channel_dedups_live_against_preloaded() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        FakeResponse(200, load_fixture("preloaded_messages.json")),
    )
    duplicate = {"id": "preloaded-1", "content": "dup", "type": "message"}
    fresh = {"id": "fresh", "content": "new", "type": "message"}
    with _session_patch(session):
        chat = _fake_chat(
            downloader,
            [
                [
                    pusher_frame(CHAT_MESSAGE_EVENT, duplicate),
                    pusher_frame(CHAT_MESSAGE_EVENT, fresh),
                ]
            ],
        )
        ids = [m["message_id"] for m in chat.chat]
        assert ids == ["preloaded-1", "preloaded-2", "fresh"]
        assert chat.diagnostics["preloaded_emitted_count"] == 2
        assert chat.diagnostics["live_emitted_count"] == 1


def test_get_chat_by_channel_filters_by_message_type() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        _empty_response(),
    )
    live_data = load_fixture("chat_message_event_data.json")
    with _session_patch(session):
        chat = _fake_chat(
            downloader,
            [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]],
            request_kwargs={"message_types": ["subscription"]},
        )
        # text_message is filtered out; nothing should be emitted.
        assert list(chat.chat) == []
        assert chat.diagnostics["preloaded_emitted_count"] == 0
        assert chat.diagnostics["live_emitted_count"] == 0


def test_get_chat_by_channel_reconnects_on_disconnect() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        _empty_response(),
        _empty_response(),
        _empty_response(),
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    frame_one = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "a", "content": "1"})
    frame_two = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "b", "content": "2"})
    with _session_patch(session):
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
    session = _live_session(
        _empty_response(),
        _empty_response(),
        _empty_response(),
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

    with _session_patch(session):
        chat = _fake_chat(
            downloader, [[*frames, ConnectionError("drop")], [final_frame]]
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
        "preloaded_emitted_count": 0,
        "live_emitted_count": 2,
        "reconnect_backfill_emitted_count": 0,
        "last_websocket_frame_timestamp": None,
    }


def test_get_chat_by_channel_emits_compact_live_events() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        _empty_response(),
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
        _session_patch(session),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 11_000]),
    ):
        chat = _fake_chat(downloader, [frames])
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
    session = _live_session(
        _empty_response(),
    )
    frames = [
        pusher_frame(POLL_UPDATE_EVENT, load_fixture("poll_update_event.json")),
        pusher_frame(POLL_DELETE_EVENT, load_fixture("poll_deleted_event.json")),
    ]

    with (
        _session_patch(session),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 12_000]),
    ):
        chat = _fake_chat(
            downloader, [frames], request_kwargs={"message_groups": ["polls"]}
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
    session = _live_session(
        _empty_response(),
    )
    frames = [
        pusher_frame(POLL_UPDATE_EVENT, load_fixture("poll_update_event.json")),
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": "visible", "type": "message", "content": "Visible"},
        ),
    ]

    with (
        _session_patch(session),
        patch.object(live_service.time, "time_ns", side_effect=[11_000, 12_000]),
    ):
        chat = _fake_chat(
            downloader, [frames], request_kwargs={"message_groups": ["messages"]}
        )
        messages = list(chat.chat)

    assert [message["message_id"] for message in messages] == ["visible"]
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
    session = _live_session(
        _empty_response(),
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
        _session_patch(session),
        patch.object(
            live_service.time,
            "time_ns",
            side_effect=[1_700_000_000_000_000_000, 1_800_000_000_000_000_000],
        ),
    ):
        chat = _fake_chat(downloader, [[missing_timestamp, provider_timestamp]])
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
    session = _live_session(
        _empty_response(),
        FakeResponse(
            200,
            _page(
                [_message("during-refresh", "recovered", "2026-01-01T00:00:08Z")], None
            ),
        ),
        _empty_response(),
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
        _session_patch(session),
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
    session = _live_session(
        _empty_response(),
        _empty_response(),
        _empty_response(),
    )
    error_frame = pusher_frame(PUSHER_ERROR, {"message": "rejected"})

    with (
        _session_patch(session),
        pytest.raises(KickError, match="protocol failure"),
    ):
        chat = _fake_chat(downloader, [[error_frame], [error_frame]])
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
    session = _live_session(
        _empty_response(),
        FakeResponse(
            200,
            _page(
                [_message("missed", "confirmed recovery", "2026-01-01T00:00:15Z")], None
            ),
        ),
        _empty_response(),
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
        assert len(session.calls) >= 5

    with (
        _session_patch(session),
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
    forward_page = _page(
        [
            _message("a", "duplicate", "2026-01-01T00:00:05Z"),
            _message("missed", "recovered", "2026-01-01T00:00:08Z"),
            _message("b", "also received live", "2026-01-01T00:00:13Z"),
        ],
        None,
    )
    session = _live_session(
        _empty_response(),
        FakeResponse(200, forward_page),
        FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
    )
    frame_one = pusher_frame(
        CHAT_MESSAGE_EVENT,
        _message("a", "before outage", "2026-01-01T00:00:05Z"),
    )
    frame_two = pusher_frame(
        CHAT_MESSAGE_EVENT,
        _message("b", "after outage", "2026-01-01T00:00:13Z"),
    )

    with (
        _session_patch(session),
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
        chat = _fake_chat(
            downloader,
            [[frame_one, ConnectionError("drop")], [frame_two]],
            request_kwargs={"message_groups": ["messages", "pins"]},
        )

        assert [message["message_id"] for message in chat.chat] == [
            "a",
            "missed",
            "b",
            "kick-pin:startup-pinned-message",
        ]

    assert session.calls[2][1]["params"] == {
        "start_time": "2026-01-01T00:00:05.000000Z"
    }
    assert session.calls[-1][1]["params"] is None
    assert chat.diagnostics["preloaded_emitted_count"] == 0
    assert chat.diagnostics["live_emitted_count"] == 1
    assert chat.diagnostics["reconnect_backfill_emitted_count"] == 3


@pytest.mark.parametrize(
    ("frames", "times", "recovered", "start", "request_kwargs", "expected"),
    [
        pytest.param(
            [("before-outage", "2026-01-01T00:00:21Z")],
            [19, 20],
            [("late-lower-timestamp", "2026-01-01T00:00:20.500000Z")],
            10,
            {},
            ["before-outage", "late-lower-timestamp"],
            id="positive-skew",
        ),
        pytest.param(
            [("before-outage", "2026-01-01T00:00:05Z")],
            [10, 20],
            [("missed", "2026-01-01T00:00:10Z")],
            5,
            {},
            ["before-outage", "missed"],
            id="negative-skew",
        ),
        pytest.param(
            [("before-outage", "2026-01-01T00:00:05Z")],
            [5.5, 12],
            [("near-confirmation", "2026-01-01T00:00:11.800000Z")],
            5,
            {},
            ["before-outage", "near-confirmation"],
            id="delivery-latency",
        ),
        pytest.param(
            [("skewed", "2026-01-01T00:00:18Z"), ("aligned", "2026-01-01T00:00:18Z")],
            [9, 18, 20],
            [("missed", "2026-01-01T00:00:18.500000Z")],
            18,
            {},
            ["skewed", "aligned", "missed"],
            id="stale-skew-replaced",
        ),
        pytest.param(
            [("bad-clock", "2036-01-01T00:00:00Z")],
            [19, 20],
            [("missed", "2026-01-01T00:00:15Z")],
            10,
            {},
            ["bad-clock", "missed"],
            id="extreme-timestamp",
        ),
        pytest.param(
            [],
            [20],
            [("missed", "2026-01-01T00:00:11Z")],
            10,
            {"message_groups": ["messages"]},
            ["missed"],
            id="ten-second-cap",
        ),
        pytest.param(
            [("no-provider-time", None)],
            [5.5, 12],
            [],
            5,
            {},
            ["no-provider-time"],
            id="receive-time-fallback",
        ),
        pytest.param(
            [("filtered", "2026-01-01T00:00:05Z")],
            [5.5, 12],
            [],
            5,
            {"message_types": ["subscription"]},
            [],
            id="filtered-checkpoint",
        ),
    ],
)
def test_reconnect_provider_clock_windows(
    frames,
    times,
    recovered,
    start,
    request_kwargs,
    expected,
) -> None:
    def message(message_id, timestamp):
        payload = {"id": message_id, "content": message_id}
        if timestamp is not None:
            payload.update(created_at=timestamp, type="message")
        return payload

    session = _live_session(
        _empty_response(),
        FakeResponse(200, _page([message(*item) for item in recovered], None)),
        _empty_response(),
    )
    with (
        _session_patch(session),
        patch.object(
            live_service.time,
            "time_ns",
            side_effect=[
                1_767_225_600_000_000_000 + int(seconds * 1_000_000_000)
                for seconds in times
            ],
        ),
    ):
        chat = _fake_chat(
            FakeDownloader(),
            [
                [
                    *(
                        pusher_frame(CHAT_MESSAGE_EVENT, message(*item))
                        for item in frames
                    ),
                    ConnectionError("drop"),
                ],
                [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
            ],
            request_kwargs=request_kwargs,
        )
        assert [message["message_id"] for message in chat.chat] == expected
    assert session.calls[2][1]["params"] == {
        "start_time": f"2026-01-01T00:00:{start:02d}.000000Z"
    }


def test_get_chat_by_channel_repeated_disconnects_exhaust_budget() -> None:
    downloader = FakeDownloader()
    session = _live_session(
        _empty_response(),
        _empty_response(),
        _empty_response(),
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    with (
        _session_patch(session),
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
    session = _WindowSession([FakeResponse(200, load_fixture("channel_offline.json"))])
    with _session_patch(session):
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
    downloader = MagicMock()
    downloader._kick_client.fetch_preloaded_chat_state.side_effect = error
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
    downloader = MagicMock()
    downloader._kick_client.fetch_preloaded_chat_state.side_effect = KeyboardInterrupt
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
    downloader = MagicMock()
    downloader._kick_client.fetch_preloaded_chat_state.return_value = (
        PreloadedChatState(
            messages=[],
            pinned_message={"duration": 1},
        )
    )
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


def test_successful_frame_shapes_survive_type_quota_and_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", "1")
    captured = []
    monkeypatch.setattr(
        live_service,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    session = _live_session(
        *[_empty_response() for _ in range(3)],
    )
    plain = [
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {
                "id": f"plain-{i}",
                "type": "message",
                "content": "plain",
            },
        )
        for i in range(3)
    ]
    diverse = [
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {
                "id": f"reply-{i}",
                "type": "reply",
                "content": "[emote:123:hello]",
                "metadata": {"original_sender": {"username": "Parent"}},
                "sender": {
                    "identity": {"badges_v2": [{"name": "level", "selected": True}]}
                },
            },
        )
        for i in range(5)
    ]
    with _session_patch(session):
        chat = _fake_chat(
            FakeDownloader(),
            [
                [*plain, *diverse[:2], ConnectionError("drop")],
                diverse[2:],
            ],
        )
        assert len(list(chat.chat)) == 8
    for shape in ("in-reply-to", "emotes", "badges"):
        calls = [
            call
            for call in captured
            if call[0][0] == f"kick-websocket-frame-text-shape-{shape}"
        ]
        assert [call[0][1] for call in calls] == diverse[:3]
        assert all(call[1] == {"sample_limit": 3} for call in calls)
    assert len(captured) == 12


def test_successful_frame_labels_ignore_missing_type() -> None:
    assert live_service._successful_frame_labels({}) == []


@pytest.mark.parametrize("optional_message", [None, "Welcome! 日本語"])
def test_compact_hosts_compose_with_live_ids_formatting_and_writers(
    tmp_path,
    optional_message,
) -> None:
    from chat_downloader.output.capture_parity import audit_capture
    from chat_downloader.runtime.chat_pipeline import configure_chat
    from chat_downloader.sites.kick.extractor import KickChatDownloader

    data = load_fixture("stream_host_event_compact.json")
    data["optional_message"] = optional_message
    session = _live_session(
        _empty_response(),
    )
    frame = pusher_frame(STREAM_HOST_EVENT, data)
    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    with (
        _session_patch(session),
        patch.object(live_service.time, "time_ns", return_value=11_000),
    ):
        chat = _fake_chat(FakeDownloader(), [[frame, frame]])
        configure_chat(
            chat,
            _request(
                message_groups=["all"], output=[str(jsonl), str(txt)], format="kick"
            ),
            KickChatDownloader(),
        )
        messages = list(chat)
        chat.close()
    assert [m["message_id"] for m in messages] == [
        "kick-stream-host:11",
        "kick-stream-host:12",
    ]
    assert [m["received_timestamp"] for m in messages] == [11, 12]
    assert all("timestamp" not in m for m in messages)
    assert chat.diagnostics["malformed_event_count"] == 0
    assert chat.diagnostics["live_emitted_count"] == 2
    suffix = f" — {optional_message}" if optional_message else ""
    expected = (
        "1970-01-01 00:00:00 [received] | [Stream host] hosting_user (1044 viewers)"
        + suffix
    )
    assert txt.read_text().splitlines() == [expected, expected]
    assert not audit_capture(
        jsonl, txt, formatter=ItemFormatter(), format_name="kick"
    ).failed

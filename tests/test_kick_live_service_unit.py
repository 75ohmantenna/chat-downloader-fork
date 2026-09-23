# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    InvalidParameter,
    RetriesExceeded,
)
from chat_downloader.formatting import ItemFormatter
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
from chat_downloader.sites.proxy import resolve_session_proxy
from tests.kick_helpers import (
    FakeDownloader,
    FakeResponse,
    FakeTransport,
    fixture_frame,
    frame_series,
    load_fixture,
    make_frame_iterator,
    message_page,
    pusher_frame,
    raw_message,
    recovery_clock,
    successful_captures,
)
from tests.kick_helpers import (
    WindowSession as _WindowSession,
)
from tests.kick_helpers import (
    build_live_chat as _build_chat,
)
from tests.kick_helpers import (
    collect_live_chat as _collect,
)
from tests.kick_helpers import (
    empty_response as _empty_response,
)
from tests.kick_helpers import (
    live_chat as _live_chat,
)
from tests.kick_helpers import (
    live_clock as _live_clock,
)
from tests.kick_helpers import (
    live_session as _live_session,
)
from tests.kick_helpers import (
    recovery_session as _recovery_session,
)
from tests.kick_helpers import (
    request as _request,
)
from tests.kick_helpers import (
    session_patch as _session_patch,
)


def _backfill(client):
    return list(
        live_service._iter_reconnect_backfill(
            MagicMock(_kick_client=client),
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            _request(max_attempts=1),
        )
    )


def _preloaded(downloader):
    return list(
        live_service._iter_preloaded_chat(
            downloader,
            "123",
            "creator",
            lambda _message: True,
        )
    )


def _reply_frame(message_id):
    return pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": message_id,
            "type": "reply",
            "content": "[emote:123:hello]",
            "metadata": {"original_sender": {"username": "Parent"}},
            "sender": {
                "identity": {"badges_v2": [{"name": "level", "selected": True}]}
            },
        },
    )


def _noise_frames():
    return [
        {"event": "pusher:connection_established", "data": "{}"},
        {"event": "App\\Events\\FutureEvent", "data": "{}"},
        {"event": CHAT_MESSAGE_EVENT, "data": "not JSON"},
    ]


class _NoRetryDownloader(FakeDownloader):
    """Downloader whose ``retry`` never sleeps or raises (drives exhaustion)."""

    @staticmethod
    def retry(*_args: Any, **_kwargs: Any) -> None:
        return None


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def record_capture(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(live_service, "capture_debug_sample", record_capture)
    monkeypatch.setattr(
        "chat_downloader.redaction.capture_debug_sample", record_capture
    )
    return calls


@pytest.fixture
def transports():
    created = []

    def factory():
        transport = FakeTransport()
        created.append(transport)
        return transport

    return created, factory


@pytest.mark.parametrize(
    ("proxies", "expected"),
    [
        ({"https": "http://proxy.example:8080"}, "http://proxy.example:8080"),
        ({}, None),
        ({"https": ""}, None),
    ],
)
def test_resolve_ws_proxy_uses_session_proxies(proxies, expected) -> None:
    session = SimpleNamespace(proxies=proxies, trust_env=False)
    assert resolve_session_proxy(session, "https://ws-us2.pusher.com") == expected


def test_resolve_ws_proxy_returns_none_without_session() -> None:
    assert resolve_session_proxy(None, "https://ws-us2.pusher.com") is None


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


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"chatroom": {"id": 1}}, "channel id"),
        (load_fixture("channel_missing_chatroom.json"), "chatroom id"),
        ({"id": "../channel", "chatroom": {"id": "room"}}, "non-numeric"),
    ],
)
def test_resolve_channel_rejects_missing_or_unsafe_ids(data, error):
    with pytest.raises(KickError, match=error):
        live_service._resolve_channel(data, "examplechannel")


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


@pytest.mark.parametrize(
    ("failures", "requested", "effective"),
    [(1, 1.0, 1.0), (0, 0.1, 1.0), (0, 2.5, 2.5)],
)
def test_open_transport_retries_and_separates_timeouts(failures, requested, effective):
    transport = FakeTransport(connect_errors=failures)
    opened = live_service._open_subscribed_transport(
        FakeDownloader(connect_timeout=7.5, read_timeout=22.0),
        "54321",
        _request(message_receive_timeout=requested),
        lambda: transport,
    )
    assert opened is transport
    assert transport.connected is True
    assert transport.subscribed_to == "54321"
    assert transport.close_count == failures


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


@pytest.mark.parametrize(
    ("forward", "preloaded", "expected"),
    [
        (
            KickForwardHistoryRejected("unsupported"),
            PreloadedChatState(
                messages=[
                    raw_message("after", "2026-01-01T00:00:21Z", "too new"),
                    raw_message("inside", "2026-01-01T00:00:15Z", "recover"),
                    raw_message("before", "2026-01-01T00:00:09Z", "too old"),
                ],
                pinned_message=load_fixture("preloaded_messages_with_pin.json")["data"][
                    "pinned_message"
                ],
            ),
            ["inside", "kick-pin:startup-pinned-message"],
        ),
        (
            message_page(
                [raw_message("inside", "2026-01-01T00:00:15Z", "recover")], cursor=None
            ),
            OSError("pin unavailable"),
            ["inside"],
        ),
        (
            message_page([], cursor=None),
            PreloadedChatState(
                messages=[
                    raw_message(
                        "preload-only", "2026-01-01T00:00:15Z", "late preload copy"
                    )
                ],
                pinned_message=None,
            ),
            ["preload-only"],
        ),
    ],
    ids=["filtered-fallback-with-pin", "pin-refresh-failure", "empty-forward-page"],
)
def test_reconnect_backfill_reconciliation(forward, preloaded, expected):
    client = MagicMock()
    for method, result in (
        (client.fetch_message_page, forward),
        (client.fetch_preloaded_chat_state, preloaded),
    ):
        if isinstance(result, Exception):
            method.side_effect = result
        else:
            method.return_value = result
    assert [message["message_id"] for message in _backfill(client)] == expected


def test_reconnect_backfill_keeps_earlier_pages_after_later_failure() -> None:
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        message_page(
            [raw_message("page-one", "2026-01-01T00:00:12Z", "preferred forward copy")],
            cursor="1767225612000000",
        ),
        KickServerError("later page failed"),
    ]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[
            raw_message("fallback-new", "2026-01-01T00:00:14Z", "fallback recovery"),
            raw_message(
                "page-one", "2026-01-01T00:00:12Z", "overlapping fallback copy"
            ),
        ],
        pinned_message=None,
    )

    messages = _backfill(client)

    assert [message["message_id"] for message in messages] == [
        "page-one",
        "fallback-new",
    ]
    assert messages[0]["message"] == "preferred forward copy"
    assert client.fetch_message_page.call_count == 2


def test_reconnect_backfill_closes_history_at_record_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator_closed = False

    def iter_history(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal generator_closed
        try:
            for index in range(5):
                yield raw_message(
                    f"message-{index}", f"2026-01-01T00:00:1{index}Z", "bounded"
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

    messages = _backfill(client)

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
        message_page(
            [
                raw_message(
                    f"old-{page}-{index}", f"2000-01-01T00:00:0{index}Z", "ignored"
                )
                for index in range(size)
            ],
            cursor=str(1767225611000000 + page * 1000000),
        )
        for page, size in enumerate(page_sizes)
    ] + [AssertionError("reconnect history limit was not enforced")]
    client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
        messages=[],
        pinned_message=None,
    )
    assert _backfill(client) == []
    assert client.fetch_message_page.call_count == len(page_sizes)


# ── end-to-end via get_chat_by_channel ────────────────────────────────────────


@pytest.mark.parametrize("duplicate", [False, True])
def test_preloaded_and_live_order_and_deduplication(duplicate):
    payloads = (
        [raw_message("preloaded-1", content="dup"), raw_message("fresh", content="new")]
        if duplicate
        else [load_fixture("chat_message_event_data.json")]
    )
    chat, messages = _collect(
        [[pusher_frame(CHAT_MESSAGE_EVENT, payload) for payload in payloads]],
        FakeResponse(200, load_fixture("preloaded_messages.json")),
    )
    assert chat.title == "Example live stream title"
    assert chat.status == "live"
    expected = "fresh" if duplicate else "live-1"
    assert [m["message_id"] for m in messages] == [
        "preloaded-1",
        "preloaded-2",
        expected,
    ]
    assert chat.diagnostics["preloaded_emitted_count"] == 2
    assert chat.diagnostics["live_emitted_count"] == 1
    assert chat.diagnostics["reconnect_backfill_emitted_count"] == 0


def test_get_chat_by_channel_preserves_live_celebration() -> None:
    celebration = load_fixture("celebration_message_event_data.json")
    chat, messages = _collect([[pusher_frame(CHAT_MESSAGE_EVENT, celebration)]])

    assert [m["message_id"] for m in messages] == ["celebration-live-1"]
    assert messages[0]["timestamp"] == 1787968059000000
    assert messages[0]["author"] == {
        "id": "88",
        "display_name": "RenewalUser",
        "name": "renewal-user",
        "colour": "#72ACED",
        "badges": [{"name": "subscriber", "title": "Subscriber", "count": 20}],
    }
    assert messages[0]["metadata"]["celebration"]["total_months"] == 20
    assert chat.diagnostics["unknown_message_type_count"] == 0


def test_get_chat_by_channel_default_transport_binds_diagnostics() -> None:
    session = _live_session()
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


def test_successful_frame_capture_requires_explicit_scope_opt_in(monkeypatch, captured):
    monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", raising=False)
    monkeypatch.setattr(
        live_service, "_successful_frame_labels", MagicMock(side_effect=AssertionError)
    )
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "live", "content": "message"},
    )

    _, messages = _collect([[frame]])
    assert [message["message_id"] for message in messages] == ["live"]

    assert captured == []


def test_successful_frame_capture_is_bounded_across_reconnects(monkeypatch, captured):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", "yes")
    message_frames = frame_series(
        CHAT_MESSAGE_EVENT,
        5,
        lambda index: {
            "id": str(index),
            "type": "message",
            "content": f"message {index}",
        },
    )
    subscription_frames = frame_series(
        SUBSCRIPTION_EVENT,
        4,
        lambda index: {"id": f"sub-{index}", "content": f"subscription {index}"},
    )

    _, messages = _collect(
        [
            [
                *_noise_frames(),
                *message_frames[:2],
                *subscription_frames[:2],
                ConnectionError("drop"),
            ],
            [*message_frames[2:], *subscription_frames[2:]],
        ],
        *[_empty_response() for _ in range(3)],
    )
    assert [m["message_id"] for m in messages] == [
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

    assert successful_captures(captured) == [
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
    message_frames = [_reply_frame(f"msg-{index}") for index in range(4)]
    subscription_frames = frame_series(
        SUBSCRIPTION_EVENT,
        4,
        lambda index: {"id": f"sub-{index}", "content": "subscription"},
    )

    _, messages = _collect([[*message_frames, *subscription_frames]])
    assert len(messages) == 8

    for label in (
        "text-message",
        "subscription",
        "text-shape-in-reply-to",
        "text-shape-emotes",
        "text-shape-badges",
    ):
        assert len(list(sample_dir.glob(f"kick-websocket-frame-{label}-*.json"))) == 3
    assert len(list(sample_dir.glob("*.json"))) == 15


@pytest.mark.parametrize("duplicate_pin", [False, True])
def test_get_chat_by_channel_emits_current_pin_once(duplicate_pin) -> None:
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
    chat, messages = _collect(
        [frames],
        FakeResponse(200, load_fixture("preloaded_messages_with_pin.json")),
        message_groups=["messages", "pins"],
    )

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


def test_get_chat_by_channel_filters_by_message_type() -> None:
    payload = load_fixture("chat_message_event_data.json")
    chat, messages = _collect(
        [[pusher_frame(CHAT_MESSAGE_EVENT, payload)]],
        message_types=["subscription"],
    )
    assert messages == []
    assert chat.diagnostics["preloaded_emitted_count"] == 0
    assert chat.diagnostics["live_emitted_count"] == 0


@pytest.mark.parametrize(
    "diagnostics", [False, True], ids=["disconnect", "diagnostics"]
)
def test_get_chat_by_channel_reconnects_on_disconnect(transports, diagnostics) -> None:
    created, factory = transports
    fields = {"type": "message"} if diagnostics else {}
    frame_one = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "a", "content": "1", **fields})
    frame_two = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "b", "content": "2", **fields})
    frames = [*(_noise_frames() if diagnostics else []), frame_one]
    with _live_chat(
        [[*frames, ConnectionError("drop")], [frame_two]],
        *[_empty_response() for _ in range(3)],
        request_kwargs={} if diagnostics else {"message_groups": ["messages"]},
        transport_factory=factory,
    ) as chat:
        assert [message["message_id"] for message in chat.chat] == ["a", "b"]
    assert len(created) == 2  # reconnected once
    assert created[0].close_count >= 1
    if not diagnostics:
        return

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
        "reconnect_backfill_truncated_count": 0,
        "reconnect_backfill_truncated_microseconds": 0,
        "last_websocket_frame_timestamp": None,
    }


def test_get_chat_by_channel_emits_compact_live_events() -> None:
    frames = [
        fixture_frame(SUBSCRIPTION_EVENT, "subscription_event_compact.json"),
        fixture_frame(
            PINNED_MESSAGE_DELETED_EVENT, "pinned_message_deleted_event_empty.json"
        ),
    ]

    with _live_clock(side_effect=[11_000, 11_000]):
        _, messages = _collect([frames])

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
    frames = [
        fixture_frame(POLL_UPDATE_EVENT, "poll_update_event.json"),
        fixture_frame(POLL_DELETE_EVENT, "poll_deleted_event.json"),
    ]

    with _live_clock(side_effect=[11_000, 12_000]):
        chat, messages = _collect([frames], message_groups=["polls"])

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
    frames = [
        fixture_frame(POLL_UPDATE_EVENT, "poll_update_event.json"),
        pusher_frame(
            CHAT_MESSAGE_EVENT,
            {"id": "visible", "type": "message", "content": "Visible"},
        ),
    ]

    with _live_clock(side_effect=[11_000, 12_000]):
        chat, messages = _collect([frames], message_groups=["messages"])

    assert [message["message_id"] for message in messages] == ["visible"]
    assert chat.diagnostics["parsed_event_count"] == 2
    assert chat.diagnostics["malformed_event_count"] == 0
    assert chat.diagnostics["malformed_event_type_counts"] == {}


def test_live_diagnostics_make_receive_timestamps_strictly_monotonic() -> None:
    diagnostics = live_service._KickLiveDiagnostics()

    with _live_clock(side_effect=[11_000, 11_000, 10_000]):
        timestamps = [diagnostics.record_frame() for _ in range(3)]

    assert timestamps == [11, 12, 13]
    assert diagnostics.summary["websocket_frame_count"] == 3
    assert diagnostics.summary["last_websocket_frame_timestamp"] == 13


def test_get_chat_by_channel_adds_distinct_receive_timestamp_fallback() -> None:
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

    with _live_clock(
        side_effect=[1_700_000_000_000_000_000, 1_800_000_000_000_000_000]
    ):
        _, messages = _collect([[missing_timestamp, provider_timestamp]])

    assert messages[0]["received_timestamp"] == 1_700_000_000_000_000
    assert "timestamp" not in messages[0]
    assert ItemFormatter().format(messages[0], format_name="kick") == (
        "2023-11-14 22:13:20 [received] | [Message deleted: deleted]"
    )
    assert messages[1]["timestamp"] == 1_749_902_400_000_000
    assert "received_timestamp" not in messages[1]


def test_get_chat_by_channel_rediscovers_key_after_pusher_error(transports) -> None:
    session = _recovery_session(
        [
            raw_message("during-refresh", "2026-01-01T00:00:08Z", "recovered"),
        ]
    )
    created, factory = transports

    live_frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "after-refresh", "content": "restored"},
    )
    with (
        recovery_clock(),
        _live_chat(
            [[pusher_frame(PUSHER_ERROR, {"message": "stale key"})], [live_frame]],
            session=session,
            transport_factory=factory,
        ) as chat,
    ):
        assert [message["message_id"] for message in chat.chat] == [
            "during-refresh",
            "after-refresh",
        ]

    assert len(created) == 2
    assert created[0].force_discover is False
    assert created[1].force_discover is True


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
    session = _recovery_session(
        [
            raw_message("missed", "2026-01-01T00:00:15Z", "confirmed recovery"),
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
        assert len(session.calls) >= 5

    with (
        _session_patch(session),
        _live_clock(return_value=1_767_225_620_000_000_000),
    ):
        chat = _build_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=frame_iterator,
        )
        assert [message["message_id"] for message in chat.chat] == ["missed"]


def test_get_chat_by_channel_backfills_messages_missed_during_reconnect() -> None:
    session = _recovery_session(
        [
            raw_message("a", "2026-01-01T00:00:05Z", "duplicate"),
            raw_message("missed", "2026-01-01T00:00:08Z", "recovered"),
            raw_message("b", "2026-01-01T00:00:13Z", "also received live"),
        ],
        pin_response=FakeResponse(
            200, load_fixture("preloaded_messages_with_pin.json")
        ),
    )
    frame_one = pusher_frame(
        CHAT_MESSAGE_EVENT,
        raw_message("a", "2026-01-01T00:00:05Z", "before outage"),
    )
    frame_two = pusher_frame(
        CHAT_MESSAGE_EVENT,
        raw_message("b", "2026-01-01T00:00:13Z", "after outage"),
    )

    with (
        recovery_clock(),
        _live_chat(
            [[frame_one, ConnectionError("drop")], [frame_two]],
            session=session,
            request_kwargs={"message_groups": ["messages", "pins"]},
        ) as chat,
    ):
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
    ("provider_times", "times", "recovered_time", "start", "filter_kind"),
    [
        (["21"], [19, 20], "20.500000", 10, None),
        (["05"], [10, 20], "10", 5, None),
        (["05"], [5.5, 12], "11.800000", 5, None),
        (["18", "18"], [9, 18, 20], "18.500000", 18, None),
        (["2036-01-01T00:00:00Z"], [19, 20], "15", 10, None),
        ([], [20], "11", 10, "messages"),
        ([None], [5.5, 12], None, 5, None),
        (["05"], [5.5, 12], None, 5, "subscription"),
    ],
    ids=[
        "positive-skew",
        "negative-skew",
        "delivery-latency",
        "stale-skew-replaced",
        "extreme-timestamp",
        "ten-second-cap",
        "receive-time-fallback",
        "filtered-checkpoint",
    ],
)
def test_reconnect_provider_clock_windows(
    provider_times, times, recovered_time, start, filter_kind
):
    def message(message_id, timestamp):
        payload = {"id": message_id, "content": message_id}
        if timestamp is not None:
            if "T" not in timestamp:
                timestamp = f"2026-01-01T00:00:{timestamp}Z"
            payload.update(created_at=timestamp, type="message")
        return payload

    frames = [(f"live-{index}", time) for index, time in enumerate(provider_times)]
    recovered = [("missed", recovered_time)] if recovered_time else []
    expected = [item[0] for item in [*frames, *recovered]]
    request_kwargs = {}
    if filter_kind == "subscription":
        request_kwargs = {"message_types": [filter_kind]}
        expected = []
    elif filter_kind:
        request_kwargs = {"message_groups": [filter_kind]}

    session = _recovery_session([message(*item) for item in recovered])
    with (
        recovery_clock(times),
        _live_chat(
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
            session=session,
            request_kwargs=request_kwargs,
        ) as chat,
    ):
        assert [message["message_id"] for message in chat.chat] == expected
    assert session.calls[2][1]["params"] == {
        "start_time": f"2026-01-01T00:00:{start:02d}.000000Z"
    }


@pytest.mark.parametrize(
    ("frame", "error", "match"),
    [
        (ConnectionError("drop"), RetriesExceeded, None),
        (
            pusher_frame(PUSHER_ERROR, {"message": "rejected"}),
            KickError,
            "protocol failure",
        ),
    ],
)
def test_repeated_recovery_failures_are_terminal(transports, frame, error, match):
    created, factory = transports
    with (
        pytest.raises(error, match=match),
        _live_chat(
            [[frame], [frame]],
            *[_empty_response() for _ in range(3)],
            transport_factory=factory,
        ) as chat,
    ):
        list(chat.chat)
    assert len(created) == 2
    assert all(transport.close_count >= 1 for transport in created)


def test_backfill_gap_diagnostics_accumulate_lost_window() -> None:
    diagnostics = live_service._KickLiveDiagnostics()
    assert diagnostics.record_backfill_gap(None, 10_000_000) == 0
    assert diagnostics.record_backfill_gap(10_000_000, 10_000_000) == 0
    assert diagnostics.record_backfill_gap(5_000_000, 10_000_000) == 5_000_000
    assert diagnostics.record_backfill_gap(8_000_000, 10_000_000) == 2_000_000
    assert diagnostics.summary["reconnect_backfill_truncated_count"] == 2
    assert diagnostics.summary["reconnect_backfill_truncated_microseconds"] == 7_000_000


def test_long_reconnect_records_uncovered_history_window() -> None:
    first = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {
            "id": "before-outage",
            "type": "message",
            "content": "first",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    session = _recovery_session([])
    with (
        recovery_clock([0, 20]),
        _live_chat(
            [
                [first, ConnectionError("drop")],
                [pusher_frame(PUSHER_SUBSCRIPTION_SUCCEEDED, {})],
            ],
            session=session,
        ) as chat,
    ):
        assert [item["message_id"] for item in chat.chat] == ["before-outage"]
    assert chat.diagnostics["reconnect_backfill_truncated_count"] == 1
    assert chat.diagnostics["reconnect_backfill_truncated_microseconds"] == 10_000_000


def test_get_chat_by_channel_offline_succeeds_with_offline_title() -> None:
    downloader = FakeDownloader()
    session = _WindowSession([FakeResponse(200, load_fixture("channel_offline.json"))])
    with _session_patch(session):
        chat = _build_chat(downloader)
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
        _build_chat(FakeDownloader(), request_kwargs=bounds)


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
    assert _preloaded(downloader) == []


def test_preloaded_history_does_not_swallow_keyboard_interrupt() -> None:
    downloader = MagicMock()
    downloader._kick_client.fetch_preloaded_chat_state.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _preloaded(downloader)


def test_preloaded_chat_captures_and_skips_malformed_current_pin(caplog, captured):
    downloader = MagicMock()
    downloader._kick_client.fetch_preloaded_chat_state.return_value = (
        PreloadedChatState(
            messages=[],
            pinned_message={"duration": 1},
        )
    )
    caplog.set_level("DEBUG", logger=live_service.logger.name)

    messages = _preloaded(downloader)

    assert messages == []
    assert "Skipping malformed Kick current pin" in caplog.text
    assert captured[0][0][0] == "kick-malformed-preloaded-pin"
    assert captured[0][0][1]["raw"] == {"duration": 1}
    assert captured[0][1]["sample_limit"] == 10


def test_successful_frame_shapes_survive_type_quota_and_reconnect(
    monkeypatch, captured
):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES", "1")
    plain = frame_series(
        CHAT_MESSAGE_EVENT,
        3,
        lambda index: raw_message(f"plain-{index}", content="plain"),
    )
    diverse = [_reply_frame(f"reply-{i}") for i in range(5)]
    _, messages = _collect(
        [[*plain, *diverse[:2], ConnectionError("drop")], diverse[2:]],
        *[_empty_response() for _ in range(3)],
    )
    assert len(messages) == 8
    for shape in ("in-reply-to", "emotes", "badges"):
        calls = successful_captures(captured, f"text-shape-{shape}")
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
    frame = pusher_frame(STREAM_HOST_EVENT, data)
    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    with (
        _live_clock(return_value=11_000),
        _live_chat([[frame, frame]]) as chat,
    ):
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

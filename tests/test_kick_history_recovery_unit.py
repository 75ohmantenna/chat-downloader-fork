# SPDX-License-Identifier: MIT

"""Composition contracts for initial and reconnect history recovery."""

from __future__ import annotations

from datetime import datetime
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from chat_downloader.sites.kick import live_service
from chat_downloader.sites.kick.api_client import PreloadedChatState
from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.errors import KickError, KickServerError
from chat_downloader.sites.kick.live_iterator import KickLiveIterator
from chat_downloader.utils.retry_utils import RetryPolicy
from chat_downloader.utils.timed_generator import TimedGenerator
from tests.kick_helpers import (
    FakeDownloader,
    FakeKickSession,
    FakeResponse,
    FakeTransport,
    build_live_chat,
    empty_response,
    load_fixture,
    message_page,
    pusher_frame,
    raw_message,
    request,
    session_patch,
)


@pytest.mark.parametrize("filtered", [False, True])
def test_initial_subscription_recovers_recorded_dense_history_before_live(filtered):
    fixture = load_fixture("history_dense_recorded.json")
    start, end = map(datetime.fromisoformat, fixture["provenance"]["capture_interval"])
    retained = {
        row["id"]: row
        for page in fixture["reverse_pages"]
        for row in page["data"]["messages"]
        if start <= datetime.fromisoformat(row["created_at"]) <= end
    }
    preload = min(retained.values(), key=lambda row: row["created_at"])
    live = fixture["reverse_pages"][0]["data"]["messages"][0]
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, message_page([preload])),
            *(FakeResponse(200, page) for page in fixture["reverse_pages"]),
            empty_response(),
        ]
    )

    def frames(_transport):
        assert len(session.calls) == 2
        yield {"event": PUSHER_SUBSCRIPTION_SUCCEEDED, "channel": "channel.12345"}
        assert len(session.calls) == 2
        yield {"event": PUSHER_SUBSCRIPTION_SUCCEEDED, "channel": "chatrooms.54321.v2"}
        assert len(session.calls) == 7
        yield pusher_frame(CHAT_MESSAGE_EVENT, live)

    with (
        session_patch(session),
        patch.object(live_service, "time", return_value=end.timestamp() - 2),
        patch.object(
            live_service, "time_ns", return_value=int(end.timestamp() * 1_000_000_000)
        ),
        patch.object(
            live_service,
            "iter_reverse_history",
            wraps=live_service.iter_reverse_history,
        ) as recovery,
    ):
        chat = build_live_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=frames,
            request_kwargs={"message_groups": ["moderation"] if filtered else ["all"]},
        )
        messages = list(chat.chat)
    assert recovery.call_count == 1
    assert recovery.call_args.args[2] == start
    assert recovery.call_args.args[3].replace(microsecond=0) == end
    assert {row["message_id"] for row in messages} == (
        set() if filtered else set(retained)
    )
    assert len(messages) == (0 if filtered else len(retained))
    assert [row["timestamp"] for row in messages] == sorted(
        row["timestamp"] for row in messages
    )
    assert chat.diagnostics["startup_backfill_emitted_count"] == (
        0 if filtered else len(retained) - 1
    )
    assert chat.diagnostics["history_recovery_failure_count"] == 0
    assert chat.diagnostics["reconnect_backfill_emitted_count"] == 0


@pytest.mark.parametrize("preload", [empty_response(), OSError("preload unavailable")])
def test_startup_recovers_capture_start_after_slow_connection_without_preload(preload):
    epoch = 1_767_225_600
    missed = raw_message("during-setup", "2026-01-01T00:00:04Z")
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            preload,
            FakeResponse(
                200,
                message_page([missed, raw_message("boundary", "2026-01-01T00:00:00Z")]),
            ),
            empty_response(),
        ]
    )
    with (
        session_patch(session),
        patch.object(live_service, "time", return_value=epoch + 0.75),
        patch.object(
            live_service, "time_ns", return_value=(epoch + 20) * 1_000_000_000
        ),
    ):
        chat = build_live_chat(
            FakeDownloader(),
            transport_factory=FakeTransport,
            frame_iterator=lambda _transport: iter(
                [{"event": PUSHER_SUBSCRIPTION_SUCCEEDED}]
            ),
        )
        assert [row["message_id"] for row in chat.chat] == ["boundary", "during-setup"]
    assert chat.diagnostics["startup_backfill_emitted_count"] == 2


def test_failed_recovery_sorts_partial_pages_and_records_failure():
    client = MagicMock()
    client.fetch_message_page.side_effect = [
        message_page(
            [raw_message("newer", "2026-01-01T00:00:19Z")], cursor="1767225619000000"
        ),
        message_page(
            [raw_message("older", "2026-01-01T00:00:12Z")], cursor="1767225612000000"
        ),
        KickError("history unavailable"),
    ]
    client.fetch_preloaded_chat_state.side_effect = OSError("snapshot unavailable")
    diagnostics = live_service._KickLiveDiagnostics()
    messages = list(
        live_service._iter_reconnect_backfill(
            MagicMock(_kick_client=client),
            "123",
            "creator",
            None,
            1_767_225_620_000_000,
            request(),
            record_diagnostic=diagnostics.increment,
        )
    )
    assert [row["message_id"] for row in messages] == ["older", "newer"]
    assert diagnostics.summary["history_recovery_failure_count"] == 1


@pytest.mark.parametrize("cancel_before_fetch", [True, False])
def test_cancelled_recovery_does_not_refresh_or_emit(cancel_before_fetch):
    cancelled = Event()
    client = MagicMock()

    def fetch(*args, **kwargs):
        cancelled.set()
        return message_page([raw_message("late", "2026-01-01T00:00:19Z")])

    client.fetch_message_page.side_effect = fetch
    if cancel_before_fetch:
        cancelled.set()
    assert (
        list(
            live_service._iter_reconnect_backfill(
                MagicMock(_kick_client=client),
                "123",
                "creator",
                None,
                1_767_225_620_000_000,
                request(),
                cancelled=cancelled,
            )
        )
        == []
    )
    client.fetch_preloaded_chat_state.assert_not_called()
    assert client.fetch_message_page.call_count == int(not cancel_before_fetch)


@pytest.mark.parametrize("retry_timeout", [0, -1])
@pytest.mark.parametrize("failure", [OSError, KickServerError])
def test_cancelled_failing_history_request_stops_worker_before_retry(
    retry_timeout, failure
):
    entered, release = Event(), Event()
    client = MagicMock()
    control = KickLiveIterator()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if client.fetch_message_page.call_count == 1:
            raise failure("request failed after cancellation")
        return message_page([raw_message("late", "2026-01-01T00:00:19Z")])

    client.fetch_message_page.side_effect = fetch
    control.source = live_service._iter_reconnect_backfill(
        MagicMock(_kick_client=client),
        "123",
        "creator",
        None,
        1_767_225_620_000_000,
        request(max_attempts=3, retry_timeout=retry_timeout),
        cancelled=control.stopped,
    )
    with (
        patch.object(RetryPolicy, "wait") as wait,
        patch.object(control, "close", wraps=control.close) as close,
    ):
        source = TimedGenerator(control)
        try:
            assert entered.wait(5)
            source.close()
            assert control.stopped.is_set()
        finally:
            # Release the in-flight request even when a regression assertion fails.
            control.request_stop()
            release.set()
            source._worker.join(5)
            source.close()
        assert not source._worker.is_alive()
        close.assert_called_once()
        assert list(source) == []
        wait.assert_not_called()
    assert client.fetch_message_page.call_count == 1
    client.fetch_preloaded_chat_state.assert_not_called()


def test_cancellation_during_pin_refresh_discards_late_results():
    cancelled = Event()
    client = MagicMock()
    client.fetch_message_page.return_value = message_page(
        [raw_message("late", "2026-01-01T00:00:19Z")]
    )

    def refresh(*args):
        cancelled.set()
        return PreloadedChatState([], None)

    client.fetch_preloaded_chat_state.side_effect = refresh
    assert (
        list(
            live_service._iter_reconnect_backfill(
                MagicMock(_kick_client=client),
                "123",
                "creator",
                None,
                1_767_225_620_000_000,
                request(),
                cancelled=cancelled,
            )
        )
        == []
    )
    client.fetch_preloaded_chat_state.assert_called_once()

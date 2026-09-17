# SPDX-License-Identifier: MIT

"""Tests for Kick VOD replay service.

The replay service primarily makes live API calls. This test suite focuses
on the pure-logic helper functions that are testable offline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import KickError, replay_service, vod_metadata

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def _page(messages, **pagination):
    return {"data": {"messages": messages, **pagination}}


def _iterate(
    client,
    *,
    start=START,
    end=END,
    channel="123",
    request=None,
    reverse=False,
    **kwargs,
):
    iterator = (
        replay_service._iter_reverse_vod_messages
        if reverse
        else replay_service._iter_vod_messages
    )
    return iterator(
        channel, start, end, request or ChatRequest(), api_client=client, **kwargs
    )


def _video_data() -> dict:
    """Return a minimal video metadata dict."""
    return {
        "id": 108462358,
        "livestream": {
            "id": 112756116,
            "session_title": "Test Stream Title",
            "start_time": "2026-06-13T00:29:45+00:00",
            "duration": 3600000,  # 1 hour
            "channel": {
                "id": 3150403,
                "chatroom": {"id": 3142359},
            },
        },
    }


@pytest.mark.parametrize("chatroom", [True, False])
def test_resolves_vod_metadata(chatroom: bool) -> None:
    data = _video_data()
    if not chatroom:
        data["livestream"]["channel"].pop("chatroom")
        data["livestream"].update(session_title="No Chatroom Stream", duration=1800000)
    channel_id, chatroom_id, title, start, end = vod_metadata._resolve_vod_window(
        data, "testuser"
    )
    assert channel_id == "3150403"
    assert chatroom_id == ("3142359" if chatroom else "")
    assert title == data["livestream"]["session_title"]
    assert start == datetime(2026, 6, 13, 0, 29, 45, tzinfo=UTC)
    assert end == start + timedelta(minutes=60 if chatroom else 30)


@pytest.mark.parametrize(
    ("livestream", "message"),
    [
        (None, "no associated livestream"),
        ({"channel": {"id": 1}}, "missing a start_time"),
        ({"start_time": "2026-01-01T00:00:00+00:00"}, "missing a channel id"),
        (
            {
                "channel": {"id": "not-a-number"},
                "start_time": "2026-01-01T00:00:00+00:00",
            },
            "non-numeric channel id",
        ),
        (
            {"channel": {"id": 1}, "start_time": "not-a-timestamp"},
            "unparsable start_time",
        ),
    ],
)
def test_invalid_vod_metadata(livestream, message) -> None:
    data = {"id": 1} if livestream is None else {"livestream": livestream}
    with pytest.raises(KickError, match=message):
        vod_metadata._resolve_vod_window(data, "testuser")


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-01-01T00:00:00", START),
        ("2026-06-13T01:29:45+01:00", datetime(2026, 6, 13, 0, 29, 45, tzinfo=UTC)),
    ],
)
def test_vod_start_time_normalized_to_utc(timestamp, expected) -> None:
    data = _video_data()
    data["livestream"]["start_time"] = timestamp
    _, _, _, start, end = vod_metadata._resolve_vod_window(data, "testuser")
    assert start.tzinfo is UTC
    assert start == expected
    assert end == start + timedelta(hours=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("start_time", "0001-01-01T00:00:00+01:00", "unusable start_time"),
        *[
            ("duration", duration, "duration")
            for duration in [float("nan"), float("inf"), 1e20]
        ],
    ],
)
def test_unusable_vod_window(field, value, message) -> None:
    data = _video_data()
    data["livestream"][field] = value
    with pytest.raises(KickError, match=message):
        vod_metadata._resolve_vod_window(data, "testuser")


@pytest.mark.parametrize(
    ("timestamp", "valid", "done"),
    [
        ("2026-01-01T00:30:00Z", True, False),
        ("2025-12-31T23:00:00Z", False, True),
        ("2026-01-01T02:00:00Z", False, False),
        ("not-a-timestamp", False, False),
        (12345, False, False),
        ("2026-01-01T00:30:00", True, False),
    ],
)
def test_classify_message_timestamp(timestamp, valid, done) -> None:
    message = _make_raw_msg("test-msg-1", timestamp)
    message["sender"] = {
        "id": 1,
        "username": "testuser",
        "slug": "testuser",
        "identity": {"color": "#fff", "badges": []},
    }
    parsed, finished = replay_service._classify_message(
        message, START, START + timedelta(hours=1)
    )
    if valid:
        assert parsed is not None
        assert parsed["message_type"] == "text_message"
    else:
        assert parsed is None
    assert finished is done


@pytest.mark.parametrize(
    "message",
    [
        {"id": "x"},
        {"created_at": "2026-01-01T00:30:00Z", "content": "test", "type": "message"},
    ],
)
def test_classify_message_missing_fields(message) -> None:
    assert replay_service._classify_message(message, START, END) == (None, False)


def test_iter_vod_messages_spools_pages_and_preserves_chronological_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page(
            [
                {"message_id": "newest-2", "payload": "x" * 128},
                {"message_id": "newest-1", "payload": "x" * 128},
            ],
            cursor="next",
        ),
        _page(
            [
                {"message_id": "oldest-2", "payload": "x" * 128},
                {"message_id": "oldest-1", "payload": "x" * 128},
            ],
            cursor=None,
        ),
    ]
    created_spools = []
    real_spool = replay_service.tempfile.SpooledTemporaryFile

    def tracking_spool(*args, **kwargs):
        spool = real_spool(*args, **kwargs)
        created_spools.append(spool)
        return spool

    monkeypatch.setattr(replay_service, "_VOD_SPOOL_MEMORY_BYTES", 1)
    monkeypatch.setattr(
        replay_service.tempfile,
        "SpooledTemporaryFile",
        tracking_spool,
    )
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end, _state: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = pages

    messages = list(
        _iterate(
            api_client,
            request=ChatRequest(max_attempts=1, interruptible_retry=False),
        )
    )

    assert [message["message_id"] for message in messages] == [
        "oldest-1",
        "oldest-2",
        "newest-1",
        "newest-2",
    ]
    assert created_spools[0]._rolled is True
    assert api_client.fetch_message_page.call_args_list[0].kwargs == {
        "cursor": replay_service._cursor_after(END)
    }


def test_iter_vod_messages_seeds_reverse_pagination_at_window_end() -> None:
    client = _client_for_page(_page([], cursor=None))
    start = START
    end = start + timedelta(days=1)
    assert list(_iterate(client, start=start, end=end)) == []
    client.fetch_message_page.assert_called_once_with(
        "123", cursor=replay_service._cursor_after(end)
    )


def test_cursor_after_treats_naive_timestamp_as_utc() -> None:
    timestamp = datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None)

    assert replay_service._cursor_after(timestamp) == "1000000"


@pytest.mark.parametrize(
    "end",
    [
        START,
        datetime(2025, 12, 31, tzinfo=UTC),
    ],
)
def test_reverse_vod_messages_does_not_fetch_empty_or_reversed_window(
    end: datetime,
) -> None:
    api_client = Mock()

    assert (
        list(
            _iterate(
                api_client,
                end=end,
                request=ChatRequest(max_attempts=1, interruptible_retry=False),
                reverse=True,
            )
        )
        == []
    )
    api_client.fetch_message_page.assert_not_called()


def test_iter_vod_messages_rejects_repeated_page_as_incomplete() -> None:
    page = _page([_make_raw_msg("same", "2026-01-01T00:01:00Z")], cursor="next")
    client = _client_for_page(page)
    with pytest.raises(KickError, match="duplicate page"):
        list(_iterate(client))


def test_iter_vod_messages_rejects_cursor_cycle_before_refetch() -> None:
    client = Mock()
    client.fetch_message_page.side_effect = [
        _page([], cursor=cursor) for cursor in ["a", "b", "a"]
    ]
    with pytest.raises(KickError, match="cursor repeated"):
        list(_iterate(client))
    assert client.fetch_message_page.call_count == 3


def test_iter_vod_messages_has_no_silent_page_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_count = 501
    pages = [
        _page(
            [{"message_id": f"message-{index}"}],
            cursor=f"cursor-{index + 1}" if index + 1 < page_count else None,
        )
        for index in range(page_count)
    ]
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end, _state: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = pages

    messages = list(
        _iterate(
            api_client,
            request=ChatRequest(max_attempts=1, interruptible_retry=False),
        )
    )

    assert len(messages) == page_count
    assert api_client.fetch_message_page.call_count == page_count


@pytest.mark.parametrize("bounded", [False, True])
def test_get_vod_chat_metadata_and_relative_bounds(bounded):
    video = _video_data()
    video["livestream"].update(
        session_title="Bounded VOD" if bounded else "Test VOD",
        start_time="2026-01-01T00:00:00+00:00",
    )
    if bounded:
        video["livestream"]["channel"].pop("chatroom")
        records = [
            _make_raw_msg("after", "2026-01-01T00:30:00Z"),
            _make_raw_msg("inside", "2026-01-01T00:20:00Z"),
            _make_raw_msg("before", "2026-01-01T00:10:00Z"),
        ]
    else:
        records = [
            {**_make_raw_msg("msg-2", "2026-01-01T00:20:00Z"), "content": "second"},
            {**_make_raw_msg("msg-1", "2026-01-01T00:10:00Z"), "content": "first"},
        ]
    client = _client_for_page(_page(records, cursor=None))
    client.fetch_video_metadata.return_value = video
    options = {"start_time": "00:15:00", "end_time": 1500} if bounded else {}
    chat = replay_service.get_vod_chat(
        "testuser",
        "vid-1",
        ChatRequest(max_attempts=1, interruptible_retry=False, **options),
        api_client=client,
    )
    assert chat.title == ("Bounded VOD" if bounded else "Test VOD")
    assert chat.status == "completed"
    assert chat.video_type == "video"
    assert chat.id == "vid-1"
    if bounded:
        assert chat.start_time == 900
        assert chat.duration == 600
    expected = ["inside"] if bounded else ["msg-1", "msg-2"]
    assert [message["message_id"] for message in chat] == expected


def test_apply_request_window_clamps_offsets_to_vod_duration() -> None:
    start = START
    end = start + timedelta(hours=1)

    selected_start, selected_end = replay_service._apply_request_window(
        start,
        end,
        ChatRequest(start_time=-10, end_time=7200),
    )

    assert selected_start == start
    assert selected_end == end


def _make_raw_msg(msg_id: str, created_at: str) -> dict[str, Any]:
    """Return a minimal raw message dict that ``parse_chat_message`` accepts."""
    return {
        "id": msg_id,
        "created_at": created_at,
        "content": "hi",
        "type": "message",
    }


def _client_for_page(page: dict[str, Any]) -> Mock:
    """Return a fake client that always returns *page* for message fetches."""
    client = Mock()
    client.fetch_message_page.return_value = page
    return client


@pytest.mark.parametrize(
    ("records", "reverse", "request_options", "expected"),
    [
        ([], True, {}, []),
        (
            [
                _make_raw_msg("newest", "2026-01-01T00:50:00Z"),
                _make_raw_msg("oldest", "2026-01-01T00:10:00Z"),
                "not-a-dict",
                _make_raw_msg("before", "2025-12-31T23:00:00Z"),
            ],
            True,
            {},
            ["oldest", "newest"],
        ),
        (
            [
                _make_raw_msg("msg-2", "2026-01-01T00:20:00Z"),
                _make_raw_msg("msg-1", "2026-01-01T00:10:00Z"),
            ],
            True,
            {"max_messages": 1},
            ["msg-1"],
        ),
        (
            [
                {
                    "created_at": "2026-01-01T00:00:00Z",
                    "content": "missing id",
                    "type": "message",
                },
                _make_raw_msg("valid", "2026-01-01T00:00:01Z"),
            ],
            False,
            {},
            ["valid"],
        ),
        (
            [
                {
                    **_make_raw_msg("excluded", "2026-01-01T00:00:00Z"),
                    "type": "subscription",
                },
                _make_raw_msg("included", "2026-01-01T00:00:01Z"),
            ],
            False,
            {"message_groups": ["messages"], "max_messages": 1},
            ["included"],
        ),
        (
            [
                _make_raw_msg("newer", "2026-01-01T00:00:02Z"),
                {
                    **_make_raw_msg("excluded", "2026-01-01T00:00:01Z"),
                    "type": "subscription",
                },
                _make_raw_msg("oldest", "2026-01-01T00:00:00Z"),
            ],
            False,
            {"message_groups": ["messages"], "max_messages": 1},
            ["oldest"],
        ),
    ],
    ids=[
        "empty",
        "window-edge",
        "limit",
        "parser-error",
        "forward-filter",
        "reverse-filter",
    ],
)
def test_vod_single_page_selection(records, reverse, request_options, expected):
    client = _client_for_page(_page(records, cursor=None))
    end = START + timedelta(hours=1) if reverse and records else END
    messages = list(
        _iterate(
            client,
            reverse=reverse,
            end=end,
            request=ChatRequest(
                max_attempts=1, interruptible_retry=False, **request_options
            ),
        )
    )
    assert [message["message_id"] for message in messages] == expected
    client.fetch_message_page.assert_called_once()


def test_reverse_replay_limits_emission_after_history_is_spooled() -> None:
    client = Mock()
    client.fetch_message_page.side_effect = [
        _page([_make_raw_msg("second", "2026-01-01T00:00:01Z")], cursor="next"),
        _page([_make_raw_msg("first", "2026-01-01T00:00:00Z")], cursor=None),
    ]
    messages = list(_iterate(client, request=ChatRequest(max_messages=1)))
    assert [item["message_id"] for item in messages] == ["first"]
    assert client.fetch_message_page.call_count == 2


def test_reverse_replay_sorts_pages_and_deduplicates_overlapping_ids() -> None:
    start = START
    client = Mock()
    client.fetch_message_page.side_effect = [
        _page(
            [
                _make_raw_msg("older", "2026-01-01T00:00:01Z"),
                _make_raw_msg("newer", "2026-01-01T00:00:02Z"),
            ],
            cursor="next",
        ),
        _page(
            [
                _make_raw_msg("older", "2026-01-01T00:00:01Z"),
                _make_raw_msg("first", "2026-01-01T00:00:00Z"),
            ],
            cursor=None,
        ),
    ]
    state = {}
    messages = list(
        _iterate(
            client,
            start=start,
            end=start + timedelta(seconds=10),
            channel="1",
            diagnostics=state,
        )
    )
    assert [item["message_id"] for item in messages] == ["first", "older", "newer"]
    assert state["duplicate_records"] == 1
    assert state["termination_reason"] == "completed"


def test_reverse_replay_rejects_nonadvancing_empty_cursor() -> None:
    start = START
    client = _client_for_page(
        _page([], cursor=replay_service._cursor_after(start + timedelta(seconds=10)))
    )
    with pytest.raises(KickError, match="backwards"):
        list(
            _iterate(
                client, start=start, end=start + timedelta(seconds=10), channel="1"
            )
        )


def test_reverse_replay_fails_on_unknown_out_of_order_cross_page_records() -> None:
    start = START
    client = Mock()
    client.fetch_message_page.side_effect = [
        _page([_make_raw_msg("earlier", "2026-01-01T00:00:01Z")], cursor="next"),
        _page([_make_raw_msg("later", "2026-01-01T00:00:02Z")], cursor=None),
    ]
    with pytest.raises(KickError, match="out of order"):
        list(
            _iterate(
                client, start=start, end=start + timedelta(seconds=10), channel="1"
            )
        )


def test_replay_close_during_request_cancels_without_fetching_another_page() -> None:
    from threading import Event, Thread

    entered, release = Event(), Event()
    client = Mock()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _page(
            [_make_raw_msg("message", "2026-01-01T00:00:01Z")], cursor="another-page"
        )

    client.fetch_message_page.side_effect = fetch
    start = START
    source = replay_service.ReplaySource(
        "1",
        start,
        start + timedelta(seconds=10),
        ChatRequest(),
        api_client=client,
        origin=start,
    )
    results = []
    worker = Thread(target=lambda: results.extend(source))
    worker.start()
    try:
        assert entered.wait(5)
        source.close()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results == []
    assert client.fetch_message_page.call_count == 1


def test_replay_cancellation_before_request_and_between_emitted_messages() -> None:
    from threading import Event

    cancelled = Event()
    cancelled.set()
    start = START
    client = Mock()

    def source():
        return _iterate(
            client,
            start=start,
            end=start + timedelta(seconds=10),
            channel="1",
            cancelled=cancelled,
        )

    assert list(source()) == []
    client.fetch_message_page.assert_not_called()
    cancelled.clear()
    client.fetch_message_page.return_value = _page(
        [
            _make_raw_msg("first", "2026-01-01T00:00:01Z"),
            _make_raw_msg("second", "2026-01-01T00:00:02Z"),
        ]
    )
    iterator = source()
    assert next(iterator)["message_id"] == "first"
    cancelled.set()
    assert list(iterator) == []


def test_replay_source_close_does_not_hide_other_generator_errors() -> None:
    start = START
    source = replay_service.ReplaySource(
        "1",
        start,
        start + timedelta(seconds=10),
        ChatRequest(),
        api_client=Mock(),
        origin=start,
    )

    def broken():
        try:
            yield {}
        finally:
            raise ValueError("different error")

    source.source = broken()
    next(source)
    with pytest.raises(ValueError, match="different error"):
        source.close()


def test_replay_reports_skip_reasons_and_progress_with_real_parser(monkeypatch):
    client = Mock()
    client.fetch_message_page.return_value = _page(
        [
            None,
            _make_raw_msg("before", "2025-12-31T23:59:59Z"),
            _make_raw_msg("after", "2026-01-01T00:00:11Z"),
            _make_raw_msg("invalid", "bad date"),
            _make_raw_msg("wrong_type", 42),
            {"created_at": "2026-01-01T00:00:01Z"},
            _make_raw_msg("valid", "2026-01-01T00:00:02Z"),
        ]
    )
    ticks = iter([0, 6, 7])
    monkeypatch.setattr(replay_service, "monotonic", lambda: next(ticks))
    logs = []
    monkeypatch.setattr(
        replay_service, "log", lambda level, text: logs.append((level, text))
    )
    state = {}
    start = START
    rows = list(
        _iterate(
            client,
            start=start,
            end=start + timedelta(seconds=10),
            channel="1",
            diagnostics=state,
        )
    )
    assert [item["message_id"] for item in rows] == ["valid"]
    assert {
        key: state[key]
        for key in (
            "before_start",
            "after_end",
            "malformed_timestamp",
            "malformed_object",
            "parse_error",
        )
    } == {
        "before_start": 1,
        "after_end": 1,
        "malformed_timestamp": 2,
        "malformed_object": 1,
        "parse_error": 1,
    }
    assert state["skipped_records"] == 6
    assert state["selected_records"] == 1
    assert len(logs) == 3
    assert all(level == "info" for level, _ in logs)
    assert "1 pages" in logs[1][1]
    assert "writing chronological" in logs[-1][1]


def test_progress_continues_through_empty_history_pages(monkeypatch):
    client = Mock()
    client.fetch_message_page.side_effect = [
        _page([], cursor="older"),
        _page([]),
    ]
    ticks = iter([0, 6, 7, 8])
    monkeypatch.setattr(replay_service, "monotonic", lambda: next(ticks))
    logs = []
    monkeypatch.setattr(replay_service, "log", lambda level, text: logs.append(text))
    start = START
    assert (
        list(
            _iterate(
                client, start=start, end=start + timedelta(seconds=10), channel="1"
            )
        )
        == []
    )
    assert "1 pages, 0 records" in logs[1]

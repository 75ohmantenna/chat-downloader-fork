# SPDX-License-Identifier: MIT

"""Offline contracts for Kick VOD windows, replay ordering, and lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import KickError, replay_service, vod_metadata
from tests.kick_helpers import message_page, raw_message

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def _at(identifier, seconds=0, **fields):
    return raw_message(
        identifier, (START + timedelta(seconds=seconds)).isoformat(), **fields
    )


def _client(*pages):
    client = Mock()
    if len(pages) == 1:
        client.fetch_message_page.return_value = pages[0]
    else:
        client.fetch_message_page.side_effect = pages
    return client


def _iterate(client, *, end=END, request=None, reverse=False, **kwargs):
    iterator = (
        replay_service._iter_reverse_vod_messages
        if reverse
        else replay_service._iter_vod_messages
    )
    return iterator(
        "123", START, end, request or ChatRequest(), api_client=client, **kwargs
    )


def _source(client):
    return replay_service.ReplaySource(
        "1",
        START,
        START + timedelta(seconds=10),
        ChatRequest(),
        api_client=client,
        origin=START,
    )


def _video_data(**fields):
    return {
        "id": 108462358,
        "livestream": {
            "id": 112756116,
            "session_title": "Test Stream Title",
            "start_time": "2026-06-13T00:29:45+00:00",
            "duration": 3600000,
            "channel": {"id": 3150403, "chatroom": {"id": 3142359}},
            **fields,
        },
    }


@pytest.mark.parametrize("chatroom", [True, False])
def test_resolves_vod_metadata(chatroom):
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
        ({"start_time": START.isoformat()}, "missing a channel id"),
        (
            {"channel": {"id": "not-a-number"}, "start_time": START.isoformat()},
            "non-numeric channel id",
        ),
        (
            {"channel": {"id": 1}, "start_time": "not-a-timestamp"},
            "unparsable start_time",
        ),
    ],
)
def test_invalid_vod_metadata(livestream, message):
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
def test_vod_start_time_normalized_to_utc(timestamp, expected):
    _, _, _, start, end = vod_metadata._resolve_vod_window(
        _video_data(start_time=timestamp), "testuser"
    )
    assert start.tzinfo is UTC
    assert start == expected
    assert end == start + timedelta(hours=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("start_time", "0001-01-01T00:00:00+01:00", "unusable start_time"),
        *[
            ("duration", value, "duration")
            for value in [float("nan"), float("inf"), 1e20]
        ],
    ],
)
def test_unusable_vod_window(field, value, message):
    with pytest.raises(KickError, match=message):
        vod_metadata._resolve_vod_window(_video_data(**{field: value}), "testuser")


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
def test_classify_message_timestamp(timestamp, valid, done):
    message = raw_message(
        "test-msg-1",
        timestamp,
        sender={
            "id": 1,
            "username": "testuser",
            "slug": "testuser",
            "identity": {"color": "#fff", "badges": []},
        },
    )
    parsed, finished = replay_service._classify_message(
        message, START, START + timedelta(hours=1)
    )
    if valid:
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
def test_classify_message_missing_fields(message):
    assert replay_service._classify_message(message, START, END) == (None, False)


def test_iter_vod_messages_spools_pages_and_preserves_chronological_order(monkeypatch):
    pages = [
        message_page(
            [
                {"message_id": f"{age}-{index}", "payload": "x" * 128}
                for index in (2, 1)
            ],
            cursor="next" if age == "newest" else None,
        )
        for age in ("newest", "oldest")
    ]
    created_spools = []
    real_spool = replay_service.tempfile.SpooledTemporaryFile

    def tracking_spool(*args, **kwargs):
        spool = real_spool(*args, **kwargs)
        created_spools.append(spool)
        return spool

    monkeypatch.setattr(replay_service, "_VOD_SPOOL_MEMORY_BYTES", 1)
    monkeypatch.setattr(replay_service.tempfile, "SpooledTemporaryFile", tracking_spool)
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end, _state: (raw, False),
    )
    client = _client(*pages)
    messages = list(_iterate(client))
    assert [row["message_id"] for row in messages] == [
        "oldest-1",
        "oldest-2",
        "newest-1",
        "newest-2",
    ]
    assert created_spools[0]._rolled is True
    assert client.fetch_message_page.call_args_list[0].kwargs == {
        "cursor": replay_service._cursor_after(END)
    }


def test_iter_vod_messages_seeds_reverse_pagination_at_window_end():
    client = _client(message_page([], cursor=None))
    assert list(_iterate(client)) == []
    client.fetch_message_page.assert_called_once_with(
        "123", cursor=replay_service._cursor_after(END)
    )


def test_cursor_after_treats_naive_timestamp_as_utc():
    naive_timestamp = datetime(1970, 1, 1)  # noqa: DTZ001 - regression needs naive input
    assert replay_service._cursor_after(naive_timestamp) == "1000000"


@pytest.mark.parametrize("end", [START, datetime(2025, 12, 31, tzinfo=UTC)])
def test_reverse_vod_messages_does_not_fetch_empty_or_reversed_window(end):
    client = Mock()
    assert list(_iterate(client, end=end, reverse=True)) == []
    client.fetch_message_page.assert_not_called()


@pytest.mark.parametrize(
    ("pages", "message", "calls"),
    [
        ([message_page([_at("same", 60)], cursor="next")], "duplicate page", None),
        ([message_page([], cursor=c) for c in ("a", "b", "a")], "cursor repeated", 3),
        (
            [message_page([], cursor=replay_service._cursor_after(END))],
            "backwards",
            None,
        ),
        (
            [
                message_page([_at("earlier", 1)], cursor="next"),
                message_page([_at("later", 2)], cursor=None),
            ],
            "out of order",
            None,
        ),
    ],
    ids=["duplicate-page", "cursor-cycle", "nonadvancing-empty", "out-of-order"],
)
def test_replay_rejects_incomplete_history(pages, message, calls):
    client = _client(*pages)
    with pytest.raises(KickError, match=message):
        list(_iterate(client))
    if calls is not None:
        assert client.fetch_message_page.call_count == calls


def test_iter_vod_messages_has_no_silent_page_ceiling(monkeypatch):
    page_count = 501
    client = _client(
        *[
            message_page(
                [{"message_id": f"message-{index}"}],
                cursor=f"cursor-{index + 1}" if index + 1 < page_count else None,
            )
            for index in range(page_count)
        ]
    )
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end, _state: (raw, False),
    )
    assert len(list(_iterate(client))) == page_count
    assert client.fetch_message_page.call_count == page_count


@pytest.mark.parametrize("bounded", [False, True])
def test_get_vod_chat_metadata_and_relative_bounds(bounded):
    video = _video_data(
        session_title="Bounded VOD" if bounded else "Test VOD",
        start_time=START.isoformat(),
    )
    if bounded:
        video["livestream"]["channel"].pop("chatroom")
        records = [_at("after", 1800), _at("inside", 1200), _at("before", 600)]
    else:
        records = [
            _at("msg-2", 1200, content="second"),
            _at("msg-1", 600, content="first"),
        ]
    client = _client(message_page(records, cursor=None))
    client.fetch_video_metadata.return_value = video
    options = {"start_time": "00:15:00", "end_time": 1500} if bounded else {}
    chat = replay_service.get_vod_chat(
        "testuser", "vid-1", ChatRequest(**options), api_client=client
    )
    assert chat.title == ("Bounded VOD" if bounded else "Test VOD")
    assert (chat.status, chat.video_type, chat.id) == ("completed", "video", "vid-1")
    if bounded:
        assert (chat.start_time, chat.duration) == (900, 600)
    assert [row["message_id"] for row in chat] == (
        ["inside"] if bounded else ["msg-1", "msg-2"]
    )


def test_apply_request_window_clamps_offsets_to_vod_duration():
    end = START + timedelta(hours=1)
    assert replay_service._apply_request_window(
        START, end, ChatRequest(start_time=-10, end_time=7200)
    ) == (START, end)


@pytest.mark.parametrize(
    ("records", "reverse", "options", "expected"),
    [
        ([], True, {}, []),
        (
            [
                _at("newest", 3000),
                _at("oldest", 600),
                "not-a-dict",
                _at("before", -3600),
            ],
            True,
            {},
            ["oldest", "newest"],
        ),
        ([_at("msg-2", 1200), _at("msg-1", 600)], True, {"max_messages": 1}, ["msg-1"]),
        (
            [
                {
                    "created_at": START.isoformat(),
                    "content": "missing id",
                    "type": "message",
                },
                _at("valid", 1),
            ],
            False,
            {},
            ["valid"],
        ),
        (
            [_at("excluded", type="subscription"), _at("included", 1)],
            False,
            {"message_groups": ["messages"], "max_messages": 1},
            ["included"],
        ),
        (
            [_at("newer", 2), _at("excluded", 1, type="subscription"), _at("oldest")],
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
def test_vod_single_page_selection(records, reverse, options, expected):
    client = _client(message_page(records, cursor=None))
    messages = _iterate(
        client,
        reverse=reverse,
        end=START + timedelta(hours=1) if reverse and records else END,
        request=ChatRequest(**options),
    )
    assert [row["message_id"] for row in messages] == expected
    client.fetch_message_page.assert_called_once()


def test_reverse_replay_limits_emission_after_history_is_spooled():
    client = _client(
        message_page([_at("second", 1)], cursor="next"),
        message_page([_at("first")], cursor=None),
    )
    assert [
        row["message_id"]
        for row in _iterate(client, request=ChatRequest(max_messages=1))
    ] == ["first"]
    assert client.fetch_message_page.call_count == 2


def test_reverse_replay_sorts_pages_and_deduplicates_overlapping_ids():
    client = _client(
        message_page([_at("older", 1), _at("newer", 2)], cursor="next"),
        message_page([_at("older", 1), _at("first")], cursor=None),
    )
    state = {}
    messages = _iterate(client, end=START + timedelta(seconds=10), diagnostics=state)
    assert [row["message_id"] for row in messages] == ["first", "older", "newer"]
    assert state["duplicate_records"] == 1
    assert state["termination_reason"] == "completed"


def test_replay_close_during_request_cancels_without_fetching_another_page():
    entered, release = Event(), Event()
    client = Mock()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return message_page([_at("message", 1)], cursor="another-page")

    client.fetch_message_page.side_effect = fetch
    source = _source(client)
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


def test_replay_cancellation_before_request_and_between_emitted_messages():
    cancelled = Event()
    cancelled.set()
    client = _client(message_page([_at("first", 1), _at("second", 2)]))

    def source():
        return _iterate(client, end=START + timedelta(seconds=10), cancelled=cancelled)

    assert list(source()) == []
    client.fetch_message_page.assert_not_called()
    cancelled.clear()
    iterator = source()
    assert next(iterator)["message_id"] == "first"
    cancelled.set()
    assert list(iterator) == []


def test_replay_source_close_does_not_hide_other_generator_errors():
    source = _source(Mock())

    def broken():
        try:
            yield {}
        finally:
            raise ValueError("different error")

    source.source = broken()
    next(source)
    with pytest.raises(ValueError, match="different error"):
        source.close()


@pytest.mark.parametrize("empty", [False, True])
def test_replay_reports_skip_reasons_and_progress(monkeypatch, empty):
    client = _client(
        *(
            [message_page([], cursor="older"), message_page([])]
            if empty
            else [
                message_page(
                    [
                        None,
                        _at("before", -1),
                        _at("after", 11),
                        raw_message("invalid", "bad date"),
                        raw_message("wrong_type", 42),
                        {"created_at": "2026-01-01T00:00:01Z"},
                        _at("valid", 2),
                    ]
                )
            ]
        )
    )
    ticks = iter([0, 6, 7, 8] if empty else [0, 6, 7])
    monkeypatch.setattr(replay_service, "monotonic", lambda: next(ticks))
    logs = []
    monkeypatch.setattr(
        replay_service, "log", lambda level, text: logs.append((level, text))
    )
    state = {}
    rows = list(_iterate(client, end=START + timedelta(seconds=10), diagnostics=state))
    assert [row["message_id"] for row in rows] == ([] if empty else ["valid"])
    if empty:
        assert "1 pages, 0 records" in logs[1][1]
    else:
        counts = {
            "before_start": 1,
            "after_end": 1,
            "malformed_timestamp": 2,
            "malformed_object": 1,
            "parse_error": 1,
        }
        assert {key: state[key] for key in counts} == counts
        assert state["skipped_records"] == 6
        assert state["selected_records"] == 1
        assert len(logs) == 3
        assert all(level == "info" for level, _ in logs)
        assert "1 pages" in logs[1][1]
        assert "writing chronological" in logs[-1][1]

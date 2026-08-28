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
from chat_downloader.sites.kick import KickError, replay_service
from chat_downloader.sites.kick.errors import KickForwardHistoryRejected


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


def _video_data_no_chatroom() -> dict:
    """Video data without chatroom info."""
    return {
        "id": 108462358,
        "livestream": {
            "id": 112756116,
            "session_title": "No Chatroom Stream",
            "start_time": "2026-06-13T00:29:45+00:00",
            "duration": 1800000,
            "channel": {"id": 3150403},
        },
    }


class TestResolveVodWindow:
    """Tests for ``_resolve_vod_window``."""

    def test_resolves_metadata(self) -> None:
        channel_id, chatroom_id, title, start_dt, end_dt = (
            replay_service._resolve_vod_window(_video_data(), "testuser")
        )
        assert channel_id == "3150403"
        assert chatroom_id == "3142359"
        assert title == "Test Stream Title"
        assert start_dt == datetime(2026, 6, 13, 0, 29, 45, tzinfo=UTC)
        assert end_dt == datetime(2026, 6, 13, 1, 29, 45, tzinfo=UTC)

    def test_missing_livestream_raises(self) -> None:
        import pytest

        data = {"id": 1}
        with pytest.raises(KickError, match="no associated livestream"):
            replay_service._resolve_vod_window(data, "testuser")

    def test_missing_start_time_raises(self) -> None:
        import pytest

        data = {
            "livestream": {
                "channel": {"id": 1},
            }
        }
        with pytest.raises(KickError, match="missing a start_time"):
            replay_service._resolve_vod_window(data, "testuser")

    def test_no_chatroom_id_falls_back_to_empty(self) -> None:
        _, chatroom_id, _, _, _ = replay_service._resolve_vod_window(
            _video_data_no_chatroom(), "testuser"
        )
        assert chatroom_id == ""

    def test_missing_channel_id_raises(self) -> None:
        data = {
            "livestream": {
                "start_time": "2026-01-01T00:00:00+00:00",
            }
        }
        with pytest.raises(KickError, match="missing a channel id"):
            replay_service._resolve_vod_window(data, "testuser")

    def test_non_numeric_channel_id_raises(self) -> None:
        data = {
            "livestream": {
                "channel": {"id": "not-a-number"},
                "start_time": "2026-01-01T00:00:00+00:00",
            }
        }
        with pytest.raises(KickError, match="non-numeric channel id"):
            replay_service._resolve_vod_window(data, "testuser")

    def test_unparsable_start_time_raises(self) -> None:
        data = {
            "livestream": {
                "channel": {"id": 1},
                "start_time": "not-a-timestamp",
            }
        }
        with pytest.raises(KickError, match="unparsable start_time"):
            replay_service._resolve_vod_window(data, "testuser")

    def test_naive_start_time_normalized_to_utc(self) -> None:
        data = {
            "livestream": {
                "channel": {"id": 1},
                "start_time": "2026-01-01T00:00:00",
                "duration": 3600000,
            }
        }
        _, _, _, start_dt, end_dt = replay_service._resolve_vod_window(data, "testuser")
        assert start_dt.tzinfo is UTC
        assert end_dt == start_dt + timedelta(hours=1)


class TestClassifyMessage:
    """Tests for ``_classify_message``."""

    def _make_msg(self, created_at: str) -> dict:
        return {
            "id": "test-msg-1",
            "content": "hello world",
            "type": "message",
            "created_at": created_at,
            "sender": {
                "id": 1,
                "username": "testuser",
                "slug": "testuser",
                "identity": {"color": "#fff", "badges": []},
            },
        }

    def test_message_in_window(self) -> None:
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
        msg = self._make_msg("2026-01-01T00:30:00Z")
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is not None
        assert parsed["message_type"] == "text_message"
        assert not done

    def test_message_before_window_returns_done(self) -> None:
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
        msg = self._make_msg("2025-12-31T23:00:00Z")
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is None
        assert done

    def test_message_after_window_skipped_not_done(self) -> None:
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
        msg = self._make_msg("2026-01-01T02:00:00Z")
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is None
        assert not done

    def test_missing_created_at_returns_not_done(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        parsed, done = replay_service._classify_message({"id": "x"}, start, end)
        assert parsed is None
        assert not done

    def test_unparseable_timestamp_skipped(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        msg = self._make_msg("not-a-timestamp")
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is None
        assert not done

    def test_non_string_created_at_skipped(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        msg = {"id": "x", "created_at": 12345}
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is None
        assert not done

    def test_malformed_message_skipped(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        # Missing 'sender' will still parse OK, but missing 'id' raises
        msg = {
            "created_at": "2026-01-01T00:30:00Z",
            "content": "test",
            "type": "message",
        }
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is None
        assert not done

    def test_naive_timestamp_normalized_to_utc(self) -> None:
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
        # created_at has no timezone info — fix normalizes it to UTC
        msg = self._make_msg("2026-01-01T00:30:00")
        parsed, done = replay_service._classify_message(msg, start, end)
        assert parsed is not None
        assert parsed["message_type"] == "text_message"
        assert not done


def test_iter_vod_messages_spools_pages_and_preserves_chronological_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "data": {
                "messages": [
                    {"message_id": "newest-2", "payload": "x" * 128},
                    {"message_id": "newest-1", "payload": "x" * 128},
                ],
                "cursor": "next",
            }
        },
        {
            "data": {
                "messages": [
                    {"message_id": "oldest-2", "payload": "x" * 128},
                    {"message_id": "oldest-1", "payload": "x" * 128},
                ],
                "cursor": None,
            }
        },
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
        lambda raw, _start, _end: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = [
        KickForwardHistoryRejected("rejected"),
        *pages,
    ]

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == [
        "oldest-1",
        "oldest-2",
        "newest-1",
        "newest-2",
    ]
    assert created_spools[0]._rolled is True
    assert api_client.fetch_message_page.call_args_list[1].kwargs == {
        "cursor": replay_service._cursor_after(datetime(2026, 1, 2, tzinfo=UTC))
    }


def test_iter_vod_messages_seeds_forward_pagination_at_window_start() -> None:
    api_client = _client_for_page({"data": {"messages": [], "cursor": None}})
    end = datetime(2026, 1, 2, tzinfo=UTC)

    assert (
        list(
            replay_service._iter_vod_messages(
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                end,
                ChatRequest(max_attempts=1, interruptible_retry=False),
                api_client=api_client,
            )
        )
        == []
    )
    api_client.fetch_message_page.assert_called_once_with(
        "123",
        start_time="2026-01-01T00:00:00.000000Z",
    )


def test_cursor_after_treats_naive_timestamp_as_utc() -> None:
    timestamp = datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None)

    assert replay_service._cursor_after(timestamp) == "1000000"


@pytest.mark.parametrize(
    "end",
    [
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2025, 12, 31, tzinfo=UTC),
    ],
)
def test_reverse_vod_messages_does_not_fetch_empty_or_reversed_window(
    end: datetime,
) -> None:
    api_client = Mock()

    assert (
        list(
            replay_service._iter_reverse_vod_messages(
                "123",
                datetime(2026, 1, 1, tzinfo=UTC),
                end,
                ChatRequest(max_attempts=1, interruptible_retry=False),
                api_client=api_client,
            )
        )
        == []
    )
    api_client.fetch_message_page.assert_not_called()


def test_iter_vod_messages_stops_repeated_cursor_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {
        "data": {
            "messages": [{"message_id": "same-message"}],
            "cursor": "stuck",
        }
    }
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = [
        KickForwardHistoryRejected("rejected"),
        page,
        page,
    ]

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=api_client,
        )
    )

    assert messages == [{"message_id": "same-message"}]
    assert api_client.fetch_message_page.call_count == 3


def test_iter_vod_messages_stops_cursor_cycle_before_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "data": {
                "messages": [{"message_id": f"message-{index}"}],
                "cursor": cursor,
            }
        }
        for index, cursor in enumerate(("cursor-a", "cursor-b", "cursor-a"))
    ]
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = [
        KickForwardHistoryRejected("rejected"),
        *pages,
    ]

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == [
        "message-2",
        "message-1",
        "message-0",
    ]
    assert api_client.fetch_message_page.call_count == 4


def test_iter_vod_messages_has_no_silent_page_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_count = 501
    pages = [
        {
            "data": {
                "messages": [{"message_id": f"message-{index}"}],
                "cursor": f"cursor-{index + 1}" if index + 1 < page_count else None,
            }
        }
        for index in range(page_count)
    ]
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end: (raw, False),
    )
    api_client = Mock()
    api_client.fetch_message_page.side_effect = [
        KickForwardHistoryRejected("rejected"),
        *pages,
    ]

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=api_client,
        )
    )

    assert len(messages) == page_count
    assert api_client.fetch_message_page.call_count == page_count + 1


class TestGetVodChat:
    """Offline tests for ``get_vod_chat``."""

    def test_builds_chat_and_yields_messages(self) -> None:
        start = "2026-01-01T00:00:00+00:00"
        video_meta = {
            "livestream": {
                "session_title": "Test VOD",
                "start_time": start,
                "duration": 3600000,
                "channel": {
                    "id": 3150403,
                    "chatroom": {"id": 3142359},
                },
            }
        }
        page = {
            "data": {
                "messages": [
                    {
                        "id": "msg-2",
                        "created_at": "2026-01-01T00:20:00Z",
                        "content": "second",
                        "type": "message",
                    },
                    {
                        "id": "msg-1",
                        "created_at": "2026-01-01T00:10:00Z",
                        "content": "first",
                        "type": "message",
                    },
                ],
                "cursor": None,
            }
        }

        api_client = Mock()
        api_client.fetch_video_metadata.return_value = video_meta
        api_client.fetch_message_page.return_value = page
        request = ChatRequest(max_attempts=1, interruptible_retry=False)

        chat = replay_service.get_vod_chat(
            "testuser",
            "vid-1",
            request,
            api_client=api_client,
        )

        assert chat.title == "Test VOD"
        assert chat.status == "completed"
        assert chat.video_type == "video"
        assert chat.id == "vid-1"

        messages = list(chat)
        assert [message["message_id"] for message in messages] == [
            "msg-1",
            "msg-2",
        ]

    def test_applies_request_relative_time_bounds(self) -> None:
        video_meta = {
            "livestream": {
                "session_title": "Bounded VOD",
                "start_time": "2026-01-01T00:00:00+00:00",
                "duration": 3600000,
                "channel": {"id": 3150403},
            }
        }
        page = {
            "data": {
                "messages": [
                    _make_raw_msg("after", "2026-01-01T00:30:00Z"),
                    _make_raw_msg("inside", "2026-01-01T00:20:00Z"),
                    _make_raw_msg("before", "2026-01-01T00:10:00Z"),
                ],
                "cursor": None,
            }
        }
        api_client = Mock()
        api_client.fetch_video_metadata.return_value = video_meta
        api_client.fetch_message_page.return_value = page
        request = ChatRequest(
            start_time="00:15:00",
            end_time=1500,
            max_attempts=1,
            interruptible_retry=False,
        )

        chat = replay_service.get_vod_chat(
            "testuser",
            "vid-1",
            request,
            api_client=api_client,
        )

        assert chat.start_time == 900
        assert chat.duration == 600
        assert [message["message_id"] for message in chat] == ["inside"]


def test_apply_request_window_clamps_offsets_to_vod_duration() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
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


def test_reverse_vod_messages_stops_on_empty_page() -> None:
    page = {"data": {"messages": [], "cursor": None}}
    api_client = _client_for_page(page)
    request = ChatRequest(max_attempts=1, interruptible_retry=False)

    messages = list(
        replay_service._iter_reverse_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            request,
            api_client=api_client,
        )
    )

    assert messages == []


def test_reverse_vod_messages_classifies_and_stops_at_window_edge() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    page = {
        "data": {
            "messages": [
                _make_raw_msg("newest", "2026-01-01T00:50:00Z"),
                _make_raw_msg("oldest", "2026-01-01T00:10:00Z"),
                "not-a-dict",
                _make_raw_msg("before", "2025-12-31T23:00:00Z"),
            ],
            "cursor": None,
        }
    }
    api_client = _client_for_page(page)
    request = ChatRequest(max_attempts=1, interruptible_retry=False)

    messages = list(
        replay_service._iter_reverse_vod_messages(
            "123",
            start,
            end,
            request,
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == [
        "oldest",
        "newest",
    ]


def test_reverse_vod_messages_respects_max_messages() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    page = {
        "data": {
            "messages": [
                _make_raw_msg("msg-2", "2026-01-01T00:20:00Z"),
                _make_raw_msg("msg-1", "2026-01-01T00:10:00Z"),
            ],
            "cursor": None,
        }
    }
    api_client = _client_for_page(page)
    request = ChatRequest(
        max_attempts=1,
        interruptible_retry=False,
        max_messages=1,
    )

    messages = list(
        replay_service._iter_reverse_vod_messages(
            "123",
            start,
            end,
            request,
            api_client=api_client,
        )
    )

    assert len(messages) == 1
    assert messages[0]["message_id"] == "msg-1"


def test_forward_vod_messages_skip_parser_failures() -> None:
    page = {
        "data": {
            "messages": [
                {
                    "created_at": "2026-01-01T00:00:00Z",
                    "content": "missing id",
                    "type": "message",
                },
                _make_raw_msg("valid", "2026-01-01T00:00:01Z"),
            ],
            "cursor": None,
        }
    }
    api_client = _client_for_page(page)

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == ["valid"]


def test_forward_vod_messages_respect_max_messages() -> None:
    pages = [
        {
            "data": {
                "messages": [_make_raw_msg("first", "2026-01-01T00:00:00Z")],
                "cursor": "1767225600000000",
            }
        },
        {
            "data": {
                "messages": [_make_raw_msg("second", "2026-01-01T00:00:01Z")],
                "cursor": None,
            }
        },
    ]
    api_client = Mock()
    api_client.fetch_message_page.side_effect = pages

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(
                max_attempts=1,
                interruptible_retry=False,
                max_messages=1,
            ),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == ["first"]
    api_client.fetch_message_page.assert_called_once()


def test_forward_vod_filtering_precedes_message_limit() -> None:
    page = {
        "data": {
            "messages": [
                {
                    **_make_raw_msg("excluded", "2026-01-01T00:00:00Z"),
                    "type": "subscription",
                },
                _make_raw_msg("included", "2026-01-01T00:00:01Z"),
            ],
            "cursor": "1767225601000000",
        }
    }
    api_client = Mock()
    api_client.fetch_message_page.return_value = page

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(
                message_groups=["messages"],
                max_messages=1,
                max_attempts=1,
                interruptible_retry=False,
            ),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == ["included"]
    api_client.fetch_message_page.assert_called_once()


def test_reverse_vod_filtering_precedes_message_limit() -> None:
    page = {
        "data": {
            "messages": [
                _make_raw_msg("newer", "2026-01-01T00:00:02Z"),
                {
                    **_make_raw_msg("excluded", "2026-01-01T00:00:01Z"),
                    "type": "subscription",
                },
                _make_raw_msg("oldest", "2026-01-01T00:00:00Z"),
            ],
            "cursor": None,
        }
    }
    api_client = Mock()
    api_client.fetch_message_page.side_effect = [
        KickForwardHistoryRejected("rejected"),
        page,
    ]

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(
                message_groups=["messages"],
                max_messages=1,
                max_attempts=1,
                interruptible_retry=False,
            ),
            api_client=api_client,
        )
    )

    assert [message["message_id"] for message in messages] == ["oldest"]

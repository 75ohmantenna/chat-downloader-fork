# SPDX-License-Identifier: MIT

"""Tests for Kick VOD replay service.

The replay service primarily makes live API calls. This test suite focuses
on the pure-logic helper functions that are testable offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import KickError, replay_service
from chat_downloader.sites.kick.errors import KickServerError


def test_fetch_with_retry_recovers_from_temporary_failure() -> None:
    fetch = Mock(side_effect=[OSError("timeout"), {"ok": True}])
    request = ChatRequest(
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
    )

    assert replay_service._fetch_with_retry(fetch, request) == {"ok": True}
    assert fetch.call_count == 2


def test_fetch_with_retry_exhausts_transient_failures() -> None:
    fetch = Mock(side_effect=KickServerError("rate limited"))
    request = ChatRequest(
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
    )

    with pytest.raises(RetriesExceeded):
        replay_service._fetch_with_retry(fetch, request)

    assert fetch.call_count == 2


def test_fetch_with_retry_does_not_retry_terminal_failure() -> None:
    fetch = Mock(side_effect=KickError("not found"))
    request = ChatRequest(max_attempts=3, retry_timeout=0)

    with pytest.raises(KickError, match="not found"):
        replay_service._fetch_with_retry(fetch, request)

    fetch.assert_called_once()


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

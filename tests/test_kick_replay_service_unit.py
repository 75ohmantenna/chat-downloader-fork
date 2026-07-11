# SPDX-License-Identifier: MIT

"""Tests for Kick VOD replay service.

The replay service primarily makes live API calls. This test suite focuses
on the pure-logic helper functions that are testable offline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import KickError, replay_service
from chat_downloader.sites.kick.errors import KickServerError

if TYPE_CHECKING:
    from collections.abc import Callable


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
        "_fetch_message_page",
        Mock(side_effect=pages),
    )
    monkeypatch.setattr(
        replay_service,
        "_classify_message",
        lambda raw, _start, _end: (raw, False),
    )

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            ChatRequest(max_attempts=1, interruptible_retry=False),
        )
    )

    assert [message["message_id"] for message in messages] == [
        "oldest-1",
        "oldest-2",
        "newest-1",
        "newest-2",
    ]
    assert created_spools[0]._rolled is True


class _FakeResponse:
    """Stub HTTP response returned by ``_FakeKickSession``."""

    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        *,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.ok = 200 <= status_code < 300
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise JSONDecodeError("bad json", "", 0)
        return self._payload


class _FakeKickSession:
    """In-memory Kick API session for testing replay service HTTP paths."""

    def __init__(
        self,
        handler: Callable[[str], Any],
    ) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        return self.handler(url)

    def close(self) -> None:
        self.closed = True


def _video_url(video_id: str) -> str:
    return f"https://kick.com/api/v1/video/{video_id}"


def _messages_url(channel_id: str) -> str:
    return f"https://kick.com/api/v2/channels/{channel_id}/messages"


class TestFetchVideoMetadata:
    """Offline tests for ``_fetch_video_metadata``."""

    def test_returns_metadata_on_success(self) -> None:
        response = _FakeResponse(200, {"id": "vod-1"})
        session = _FakeKickSession(lambda url: response)

        data = replay_service._fetch_video_metadata(
            "vod-1",
            session=session,
        )

        assert data == {"id": "vod-1"}
        assert session.calls[0][0] == _video_url("vod-1")

    def test_404_raises_kick_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(404, {}))

        with pytest.raises(KickError, match="not found"):
            replay_service._fetch_video_metadata("vod-1", session=session)

    def test_429_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(429, {}))

        with pytest.raises(KickServerError, match="429"):
            replay_service._fetch_video_metadata("vod-1", session=session)

    def test_5xx_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(500, {}))

        with pytest.raises(KickServerError, match="500"):
            replay_service._fetch_video_metadata("vod-1", session=session)

    def test_other_error_raises_kick_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(400, {}))

        with pytest.raises(KickError, match="400"):
            replay_service._fetch_video_metadata("vod-1", session=session)

    def test_non_object_response_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(200, ["bad"]))

        with pytest.raises(KickServerError, match="JSON object"):
            replay_service._fetch_video_metadata("vod-1", session=session)

    def test_creates_and_closes_session_when_none_provided(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_session = _FakeKickSession(lambda url: _FakeResponse(200, {"id": "vod-1"}))
        monkeypatch.setattr(
            replay_service,
            "_get_kick_session",
            lambda **kwargs: fake_session,
        )

        data = replay_service._fetch_video_metadata("vod-1", session=None)

        assert data == {"id": "vod-1"}
        assert fake_session.closed


class TestFetchMessagePage:
    """Offline tests for ``_fetch_message_page``."""

    def test_returns_page_data(self) -> None:
        page = {"data": {"messages": [], "cursor": "next"}}
        session = _FakeKickSession(lambda url: _FakeResponse(200, page))

        data = replay_service._fetch_message_page(
            "123",
            "cursor",
            session=session,
        )

        assert data == page
        assert _messages_url("123") in session.calls[0][0]
        assert session.calls[0][1].get("params") == {"cursor": "cursor"}

    def test_malformed_json_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(200, json_error=True))

        with pytest.raises(KickServerError, match="malformed JSON"):
            replay_service._fetch_message_page("123", session=session)

    def test_429_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(429, {}))

        with pytest.raises(KickServerError, match="429"):
            replay_service._fetch_message_page("123", session=session)

    def test_5xx_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(500, {}))

        with pytest.raises(KickServerError, match="500"):
            replay_service._fetch_message_page("123", session=session)

    def test_non_server_error_raises_kick_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(403, {}))

        with pytest.raises(KickError, match="403"):
            replay_service._fetch_message_page("123", session=session)

    def test_non_object_response_raises_kick_server_error(self) -> None:
        session = _FakeKickSession(lambda url: _FakeResponse(200, "bad"))

        with pytest.raises(KickServerError, match="non-object"):
            replay_service._fetch_message_page("123", session=session)

    def test_creates_and_closes_session_when_none_provided(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = {"data": {"messages": [], "cursor": None}}
        fake_session = _FakeKickSession(lambda url: _FakeResponse(200, page))
        monkeypatch.setattr(
            replay_service,
            "_get_kick_session",
            lambda **kwargs: fake_session,
        )

        data = replay_service._fetch_message_page("123", session=None)

        assert data == page
        assert fake_session.closed


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

        def handler(url: str) -> _FakeResponse:
            if "/video/" in url:
                return _FakeResponse(200, video_meta)
            return _FakeResponse(200, page)

        session = _FakeKickSession(handler)
        request = ChatRequest(max_attempts=1, interruptible_retry=False)

        chat = replay_service.get_vod_chat(
            "testuser",
            "vid-1",
            request,
            session=session,
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


def _make_raw_msg(msg_id: str, created_at: str) -> dict[str, Any]:
    """Return a minimal raw message dict that ``parse_chat_message`` accepts."""
    return {
        "id": msg_id,
        "created_at": created_at,
        "content": "hi",
        "type": "message",
    }


def _iter_session_for_page(page: dict[str, Any]) -> _FakeKickSession:
    """Return a fake session that always returns *page* for message fetches."""
    return _FakeKickSession(lambda url: _FakeResponse(200, page))


def test_iter_vod_messages_stops_on_empty_page() -> None:
    page = {"data": {"messages": [], "cursor": None}}
    session = _iter_session_for_page(page)
    request = ChatRequest(max_attempts=1, interruptible_retry=False)

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            request,
            session=session,
        )
    )

    assert messages == []


def test_iter_vod_messages_classifies_and_stops_at_window_edge() -> None:
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
    session = _iter_session_for_page(page)
    request = ChatRequest(max_attempts=1, interruptible_retry=False)

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            start,
            end,
            request,
            session=session,
        )
    )

    assert [message["message_id"] for message in messages] == [
        "oldest",
        "newest",
    ]


def test_iter_vod_messages_respects_max_messages() -> None:
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
    session = _iter_session_for_page(page)
    request = ChatRequest(
        max_attempts=1,
        interruptible_retry=False,
        max_messages=1,
    )

    messages = list(
        replay_service._iter_vod_messages(
            "123",
            start,
            end,
            request,
            session=session,
        )
    )

    assert len(messages) == 1
    assert messages[0]["message_id"] == "msg-1"

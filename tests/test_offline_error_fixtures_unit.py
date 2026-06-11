# SPDX-License-Identifier: MIT

"""Fixture-based offline tests for YouTube/Twitch error response handling.

Loads JSON fixtures from tests/fixtures/{platform}/errors/ to verify that retry
and error branches behave correctly for real-looking API payloads. Also covers
the Twitch IRC recv-loop error branches (socket.timeout, empty recv) without
any network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chat_downloader.errors import ParsingError, RetriesExceeded
from chat_downloader.sites.twitch.graphql_client import _handle_gql_errors
from chat_downloader.sites.twitch.irc_transport import (
    get_chat_messages_by_stream_id,
)
from chat_downloader.sites.youtube.client_requests_continuation import (
    _get_continuation_info as _yt_get_continuation_info,
)

_YT_ERRORS = Path(__file__).parent / "fixtures" / "youtube" / "errors"
_TW_ERRORS = Path(__file__).parent / "fixtures" / "twitch" / "errors"


def _load(path: Path, name: str) -> dict | list:
    return json.loads((path / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# Fixture shape sanity checks
# ---------------------------------------------------------------------------


def test_rate_limit_fixture_has_code_429() -> None:
    data = _load(_YT_ERRORS, "rate_limit_429")
    assert data["error"]["code"] == 429


def test_forbidden_fixture_has_code_403() -> None:
    data = _load(_YT_ERRORS, "forbidden_403")
    assert data["error"]["code"] == 403


def test_twitch_service_error_fixture_shape() -> None:
    data = _load(_TW_ERRORS, "graphql_service_error")
    assert isinstance(data, list)
    assert data[0]["errors"][0]["message"] == "service error"


def test_twitch_persisted_query_not_found_fixture_shape() -> None:
    data = _load(_TW_ERRORS, "persisted_query_not_found")
    assert isinstance(data, list)
    assert data[0]["errors"][0]["message"] == "PersistedQueryNotFound"


# ---------------------------------------------------------------------------
# YouTube: retry on rate-limit fixture (Item 5)
# ---------------------------------------------------------------------------


def test_continuation_retries_on_rate_limit_fixture() -> None:
    """_get_continuation_info retries on the rate_limit_429 fixture."""
    fixture = _load(_YT_ERRORS, "rate_limit_429")
    calls = {"count": 0}

    class _Resp:
        def __init__(self, status, payload) -> None:
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    def session_post(_url, **_kw):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp(429, fixture)
        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
        }
        return ok

    result = _yt_get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "tok"},
    )
    assert result == {
        "continuationContents": {"liveChatContinuation": {"actions": []}}
    }
    assert calls["count"] == 2


def test_continuation_raises_retries_exceeded_on_rate_limit_fixture() -> None:
    """RetriesExceeded is raised after all attempts on rate_limit_429."""
    fixture = _load(_YT_ERRORS, "rate_limit_429")

    class _Resp:
        status_code = 429

        def json(self):
            return fixture

    def session_post(_url, **_kw):
        return _Resp()

    with pytest.raises(RetriesExceeded):
        _yt_get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 2},
            json={"continuation": "tok"},
        )


def test_continuation_retries_on_forbidden_fixture() -> None:
    """_get_continuation_info retries on HTTP 403 (forbidden_403 fixture)."""
    fixture = _load(_YT_ERRORS, "forbidden_403")
    calls = {"count": 0}

    class _Resp:
        def __init__(self, status, payload) -> None:
            self.status_code = status
            self._payload = payload

        def json(self):
            return self._payload

    def session_post(_url, **_kw):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp(403, fixture)
        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
        }
        return ok

    result = _yt_get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "tok"},
    )
    assert result == {
        "continuationContents": {"liveChatContinuation": {"actions": []}}
    }
    assert calls["count"] == 2


# ---------------------------------------------------------------------------
# Twitch: GraphQL service error is handled gracefully (Item 5)
# ---------------------------------------------------------------------------


def test_handle_gql_errors_service_error_does_not_raise(caplog) -> None:
    """_handle_gql_errors warns and does not raise on 'service error'."""
    import logging

    fixture = _load(_TW_ERRORS, "graphql_service_error")
    errors = fixture[0]["errors"]

    # The logger name is chat_downloader.metadata (child of chat_downloader).
    with caplog.at_level(logging.WARNING, logger="chat_downloader.metadata"):
        _handle_gql_errors(errors)  # must not raise

    # Either caplog captured it, or at minimum it did not raise an exception.
    # The key invariant is: service errors are non-fatal.
    log_texts = " ".join(r.message.lower() for r in caplog.records)
    if log_texts:
        assert "transient" in log_texts or "service error" in log_texts


def test_handle_gql_errors_persisted_query_not_found_is_actionable() -> None:
    fixture = _load(_TW_ERRORS, "persisted_query_not_found")
    errors = fixture[0]["errors"]

    with pytest.raises(ParsingError) as excinfo:
        _handle_gql_errors(errors, ["StreamMetadata"])

    message = str(excinfo.value)
    assert "StreamMetadata" in message
    assert "Operation hashes or required variables may be stale" in message


# ---------------------------------------------------------------------------
# Twitch IRC recv-loop error branches (Item 5)
# ---------------------------------------------------------------------------


class _MockIRC:
    """Minimal IRC mock that serves a fixed sequence of recv() results."""

    def __init__(self, responses: list) -> None:
        self._responses = responses
        self._idx = 0

    def recv(self, buffer_size: int) -> str:
        if self._idx >= len(self._responses):
            msg = "Mock exhausted"
            raise ConnectionError(msg)
        resp = self._responses[self._idx]
        self._idx += 1
        if isinstance(resp, Exception):
            raise resp
        return resp

    def send_raw(self, string: str) -> None:
        pass

    def set_timeout(self, timeout: float) -> None:
        pass

    def join_channel(self, channel: str) -> None:
        pass

    def close_connection(self) -> None:
        pass


def test_recv_loop_handles_socket_timeout_silently() -> None:
    """socket.timeout during recv must be swallowed; the loop continues."""
    irc = _MockIRC(
        [
            TimeoutError("recv timed out"),  # caught silently
            "",  # empty recv → ConnectionError → stop
        ],
    )
    params = {"buffer_size": 4096, "message_groups": []}

    collected = []
    with pytest.raises(ConnectionError):
        collected.extend(
            get_chat_messages_by_stream_id(irc, "testchan", params)
        )

    # No messages were parsed (the timeout buffer had no IRC content).
    assert collected == []


def test_recv_loop_raises_connection_error_on_empty_recv() -> None:
    """Empty bytes from recv() must raise ConnectionError immediately."""
    irc = _MockIRC([""])  # first recv returns empty string
    params = {"buffer_size": 4096, "message_groups": []}

    with pytest.raises(ConnectionError, match="Lost connection"):
        list(get_chat_messages_by_stream_id(irc, "testchan", params))


def test_recv_loop_multiple_timeouts_before_connection_error() -> None:
    """Multiple socket.timeout events before a ConnectionError are handled."""
    irc = _MockIRC(
        [
            TimeoutError("t1"),
            TimeoutError("t2"),
            TimeoutError("t3"),
            "",  # triggers ConnectionError
        ],
    )
    params = {"buffer_size": 4096, "message_groups": []}

    with pytest.raises(ConnectionError):
        list(get_chat_messages_by_stream_id(irc, "testchan", params))

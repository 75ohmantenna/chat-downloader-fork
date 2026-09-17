# SPDX-License-Identifier: MIT

"""Fixture and malformed-response contracts for continuation parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_downloader.errors import IncompleteContinuationError
from chat_downloader.sites.youtube.continuations import (
    ContinuationParseResult,
    parse_continuation_response,
    summarize_continuation_payload,
)
from tests.youtube_third_helpers import response, wrap


def test_continuation_result_preserves_positional_debug_info():
    debug_info = {"continuation_key": "timedContinuationData"}
    result = ContinuationParseResult([], "next", 1000, False, debug_info)

    assert result.debug_info is debug_info
    assert result.click_tracking_params is None


def _payload(kind="timedContinuationData", *, actions=(), **continuation):
    return response(
        actions, continuations=[{kind: {"continuation": "TOK", **continuation}}]
    )


@pytest.mark.parametrize(
    ("fixture", "token", "timeout", "count", "end", "debug_key"),
    [
        (
            "standard_with_token",
            "NEXT_TOKEN_ABC123",
            5000,
            2,
            False,
            "timedContinuationData",
        ),
        (
            "invalidation_continuation",
            "LIVE_INVALIDATION_TOKEN_456",
            3000,
            1,
            False,
            "invalidationContinuationData",
        ),
        ("terminal_no_continuation", None, None, 1, True, None),
        ("seek_continuation_only", None, None, 0, True, None),
        ("no_actions_live_heartbeat", "HEARTBEAT_TOKEN_NEXT", 5000, 0, False, None),
        ("timeout_clamping_large", "CLAMPED_TIMEOUT_TOKEN", 20000, None, False, None),
    ],
)
def test_continuation_fixtures(fixture, token, timeout, count, end, debug_key):
    path = Path(__file__).parent / "fixtures/youtube/continuations" / f"{fixture}.json"
    result = parse_continuation_response(json.loads(path.read_text()))
    assert (result.next_continuation, result.timeout_ms, result.is_end) == (
        token,
        timeout,
        end,
    )
    assert isinstance(result.actions, list)
    if count is not None:
        assert len(result.actions) == count
    if debug_key is not None:
        assert result.debug_info.get("continuation_key") == debug_key


@pytest.mark.parametrize(
    ("payload", "match", "detail"),
    [
        ({}, "Unrecognized YouTube continuation", None),
        (
            wrap("continuationContents.somethingElse", {}),
            "Unrecognized YouTube continuation response shape",
            "continuation_contents_keys",
        ),
        (
            {"error": {"code": 400, "message": "Chat disabled"}},
            "contains an API error payload",
            "Chat disabled",
        ),
    ],
)
def test_incomplete_continuation(payload, match, detail):
    with pytest.raises(IncompleteContinuationError, match=match) as exc:
        parse_continuation_response(payload)
    if detail:
        assert detail in str(exc.value)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"timeoutMs": 0}, 0),
        ({"timeoutMs": -500}, -500),
        ({}, None),
        ({"timeout_ms": 1500}, 1500),
        ({"pollingIntervalMillis": 2500}, 2500),
        *[
            ({"timeoutMs": value}, None)
            for value in ("soon", [], {}, None, True, False)
        ],
    ],
)
def test_timeout_hints(fields, expected):
    assert parse_continuation_response(_payload(**fields)).timeout_ms == expected


@pytest.mark.parametrize(
    ("kind", "fields", "actions"),
    [
        ("reloadContinuationData", {"continuation": "RELOAD_TOK"}, []),
        (
            "liveChatReplayContinuationData",
            {"continuation": "REPLAY_TOK", "timeoutMs": 2000},
            [{"addChatItemAction": {}}],
        ),
    ],
)
def test_continuation_types(kind, fields, actions):
    result = parse_continuation_response(_payload(kind, actions=actions, **fields))
    assert result.next_continuation == fields["continuation"]
    assert result.timeout_ms == fields.get("timeoutMs")
    assert result.is_end is False
    assert result.actions == actions


def test_unknown_continuation_preserves_summary():
    result = parse_continuation_response(
        _payload(
            "futureContinuationData",
            actions=[{"addChatItemAction": {}}],
            timeoutMs=2000,
        )
    )
    assert result.is_end is False
    assert result.debug_info["unknown"] is True
    assert result.debug_info["payload_summary"] == {
        "top_level_keys": ["continuationContents"],
        "continuation_contents_keys": ["liveChatContinuation"],
        "live_chat_keys": ["actions", "continuations"],
        "actions_count": 1,
        "continuation_keys": ["futureContinuationData"],
    }


def test_error_summary():
    error = {"code": 429, "message": "Rate limited"}
    assert summarize_continuation_payload({"error": error}) == {
        "top_level_keys": ["error"],
        "error": error,
    }


@pytest.mark.parametrize("actions", [{"not": "a list"}, "string"])
def test_non_list_actions(actions):
    payload = wrap("continuationContents.liveChatContinuation.actions", actions)
    assert parse_continuation_response(payload).actions == []

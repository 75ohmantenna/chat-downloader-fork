# SPDX-License-Identifier: MIT

"""Fixture-based unit tests for parse_continuation_response().

Each JSON fixture under tests/fixtures/youtube/continuations/ represents a
distinct payload shape returned by
/youtubei/v1/live_chat/get_live_chat[_replay].  Tests load the fixture, call
parse_continuation_response(), and assert the fields of ContinuationParseResult
match expectations.
"""

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

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "youtube" / "continuations"


def _load(name: str) -> dict:
    """Load a JSON fixture by filename (without .json extension)."""
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text())


def _payload(kind="timedContinuationData", *, actions=None, **continuation):
    return {
        "continuationContents": {
            "liveChatContinuation": {
                "actions": [] if actions is None else actions,
                "continuations": [{kind: {"continuation": "TOK", **continuation}}],
            }
        }
    }


# ---------------------------------------------------------------------------
# Parametrized fixture-based tests
#
# Columns: fixture_name, expected_token, expected_timeout_ms,
#          expected_actions_len (None = don't assert), expected_is_end,
#          expected_debug_key (None = don't assert)
# ---------------------------------------------------------------------------

_CONTINUATION_CASES = [
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
    (
        "timeout_clamping_large",
        "CLAMPED_TIMEOUT_TOKEN",
        20000,
        None,
        False,
        None,
    ),
]


@pytest.mark.parametrize(
    (
        "fixture_name",
        "expected_token",
        "expected_timeout_ms",
        "expected_actions_len",
        "expected_is_end",
        "expected_debug_key",
    ),
    _CONTINUATION_CASES,
    ids=[c[0] for c in _CONTINUATION_CASES],
)
def test_continuation_parsing(
    fixture_name: str,
    expected_token: str | None,
    expected_timeout_ms: int | None,
    expected_actions_len: int | None,
    expected_is_end: bool,
    expected_debug_key: str | None,
) -> None:
    result = parse_continuation_response(_load(fixture_name))

    assert isinstance(result, ContinuationParseResult)
    assert isinstance(result.actions, list)
    assert result.next_continuation == expected_token
    assert result.timeout_ms == expected_timeout_ms
    assert result.is_end is expected_is_end

    if expected_actions_len is not None:
        assert len(result.actions) == expected_actions_len

    if expected_debug_key is not None:
        assert result.debug_info.get("continuation_key") == expected_debug_key


# ---------------------------------------------------------------------------
# Programmatic edge-case tests (no fixture files needed)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_payload_raises_incomplete_continuation_error(self) -> None:
        with pytest.raises(
            IncompleteContinuationError,
            match="Unrecognized YouTube continuation",
        ):
            parse_continuation_response({})

    def test_payload_without_live_chat_raises_incomplete_continuation_error(
        self,
    ) -> None:
        payload = {"continuationContents": {"somethingElse": {}}}
        with pytest.raises(
            IncompleteContinuationError,
            match="Unrecognized YouTube continuation response shape",
        ) as exc_info:
            parse_continuation_response(payload)
        assert "continuation_contents_keys" in str(exc_info.value)

    def test_error_payload_raises_incomplete_continuation_error(self) -> None:
        payload = {"error": {"code": 400, "message": "Chat disabled"}}
        with pytest.raises(
            IncompleteContinuationError,
            match="contains an API error payload",
        ) as exc_info:
            parse_continuation_response(payload)
        assert "Chat disabled" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"timeoutMs": 0}, 0),
            ({"timeoutMs": -500}, -500),
            ({}, None),
            ({"timeout_ms": 1500}, 1500),
            ({"pollingIntervalMillis": 2500}, 2500),
            ({"timeoutMs": "soon"}, None),
            ({"timeoutMs": []}, None),
            ({"timeoutMs": {}}, None),
            ({"timeoutMs": None}, None),
            ({"timeoutMs": True}, None),
            ({"timeoutMs": False}, None),
        ],
    )
    def test_timeout_hints(self, fields, expected) -> None:
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
    def test_chat_continuation_types(self, kind, fields, actions) -> None:
        result = parse_continuation_response(_payload(kind, actions=actions, **fields))
        assert result.next_continuation == fields["continuation"]
        assert result.timeout_ms == fields.get("timeoutMs")
        assert result.is_end is False
        assert result.actions == actions

    def test_unknown_continuation_preserves_payload_summary(self) -> None:
        payload = _payload(
            "futureContinuationData",
            actions=[{"addChatItemAction": {}}],
            timeoutMs=2000,
        )
        result = parse_continuation_response(payload)
        assert result.is_end is False
        assert result.debug_info["unknown"] is True
        assert result.debug_info["payload_summary"] == {
            "top_level_keys": ["continuationContents"],
            "continuation_contents_keys": ["liveChatContinuation"],
            "live_chat_keys": ["actions", "continuations"],
            "actions_count": 1,
            "continuation_keys": ["futureContinuationData"],
        }

    def test_summarize_continuation_payload_handles_error_payload(self) -> None:
        assert summarize_continuation_payload(
            {"error": {"code": 429, "message": "Rate limited"}},
        ) == {
            "top_level_keys": ["error"],
            "error": {"code": 429, "message": "Rate limited"},
        }

    @pytest.mark.parametrize("actions", [{"not": "a list"}, "string"])
    def test_non_list_actions_return_empty(self, actions) -> None:
        payload = {
            "continuationContents": {"liveChatContinuation": {"actions": actions}}
        }
        assert parse_continuation_response(payload).actions == []

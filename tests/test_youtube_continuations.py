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

    def test_timeout_zero_when_raw_is_zero(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "timeoutMs": 0,
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms == 0

    def test_timeout_preserves_negative_value(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "timeoutMs": -500,
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms == -500

    def test_no_timeout_field_returns_none(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms is None

    def test_timeout_ms_extracts_timeout_ms_alias(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "timeout_ms": 1500,
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms == 1500

    def test_timeout_ms_extracts_polling_interval_millis(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "pollingIntervalMillis": 2500,
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms == 2500

    def test_invalid_timeout_field_returns_none(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "continuations": [
                        {
                            "timedContinuationData": {
                                "timeoutMs": "soon",
                                "continuation": "TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.timeout_ms is None

    def test_reload_continuation_data_is_recognized(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [],
                    "continuations": [
                        {
                            "reloadContinuationData": {
                                "continuation": "RELOAD_TOK",
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.next_continuation == "RELOAD_TOK"
        assert result.is_end is False

    def test_live_chat_replay_continuation_data_is_recognized(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [{"addChatItemAction": {}}],
                    "continuations": [
                        {
                            "liveChatReplayContinuationData": {
                                "continuation": "REPLAY_TOK",
                                "timeoutMs": 2000,
                            },
                        },
                    ],
                },
            },
        }
        result = parse_continuation_response(payload)
        assert result.next_continuation == "REPLAY_TOK"
        assert result.timeout_ms == 2000
        assert len(result.actions) == 1

    def test_unknown_continuation_preserves_payload_summary(self) -> None:
        payload = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [{"addChatItemAction": {}}],
                    "continuations": [
                        {
                            "futureContinuationData": {
                                "continuation": "TOK",
                                "timeoutMs": 2000,
                            },
                        },
                    ],
                },
            },
        }
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

    def test_extract_actions_non_list_returns_empty(self) -> None:
        """Non-list ``actions`` value (malformed payload) yields empty list."""
        from chat_downloader.sites.youtube.continuations import _extract_actions

        assert _extract_actions({"actions": {"not": "a list"}}) == []
        assert _extract_actions({"actions": "string"}) == []

    def test_extract_timeout_ms_non_numeric_type_returns_none(self) -> None:
        """A list/dict timeout value (unexpected JSON type) returns None."""
        from chat_downloader.sites.youtube.continuations import _extract_timeout_ms

        assert _extract_timeout_ms([]) is None
        assert _extract_timeout_ms({}) is None

    def test_extract_timeout_ms_none_returns_none(self) -> None:
        from chat_downloader.sites.youtube.continuations import _extract_timeout_ms

        assert _extract_timeout_ms(None) is None

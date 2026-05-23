# SPDX-License-Identifier: MIT

"""Fixture-based unit tests for parse_continuation_response().

Each JSON fixture under tests/fixtures/youtube/continuations/ represents a
distinct payload shape returned by
/youtubei/v1/live_chat/get_live_chat[_replay].  Tests load the fixture, call
parse_continuation_response(), and assert the fields of ContinuationParseResult
match expectations.
"""

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
# Standard continuation with actions and continuation token
# ---------------------------------------------------------------------------


class TestStandardWithToken:
    """standard_with_token.json — timedContinuationData with two actions."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(_load("standard_with_token"))

    def test_returns_parse_result(self) -> None:
        assert isinstance(self.result, ContinuationParseResult)

    def test_actions_non_empty(self) -> None:
        assert len(self.result.actions) == 2

    def test_actions_is_list(self) -> None:
        assert isinstance(self.result.actions, list)

    def test_next_continuation_exact_value(self) -> None:
        assert self.result.next_continuation == "NEXT_TOKEN_ABC123"

    def test_timeout_ms_exact_value(self) -> None:
        assert self.result.timeout_ms == 5000

    def test_is_end_false(self) -> None:
        assert self.result.is_end is False

    def test_debug_info_has_continuation_key(self) -> None:
        assert (
            self.result.debug_info.get("continuation_key")
            == "timedContinuationData"
        )


# ---------------------------------------------------------------------------
# Invalidation continuation (live stream)
# ---------------------------------------------------------------------------


class TestInvalidationContinuation:
    """invalidation_continuation.json — invalidationContinuationData shape."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(
            _load("invalidation_continuation")
        )

    def test_next_continuation_exact_value(self) -> None:
        assert self.result.next_continuation == "LIVE_INVALIDATION_TOKEN_456"

    def test_timeout_ms(self) -> None:
        assert self.result.timeout_ms == 3000

    def test_actions_non_empty(self) -> None:
        assert len(self.result.actions) == 1

    def test_is_end_false(self) -> None:
        assert self.result.is_end is False

    def test_debug_info_key(self) -> None:
        assert (
            self.result.debug_info.get("continuation_key")
            == "invalidationContinuationData"
        )


# ---------------------------------------------------------------------------
# Terminal response — no continuation list entries
# ---------------------------------------------------------------------------


class TestTerminalNoContinuation:
    """terminal_no_continuation.json — empty continuations list."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(
            _load("terminal_no_continuation")
        )

    def test_next_continuation_is_none(self) -> None:
        assert self.result.next_continuation is None

    def test_is_end_true(self) -> None:
        assert self.result.is_end is True

    def test_actions_present(self) -> None:
        # Actions can still be present in the last page.
        assert len(self.result.actions) == 1

    def test_timeout_ms_is_none(self) -> None:
        assert self.result.timeout_ms is None


# ---------------------------------------------------------------------------
# Seek-continuation-only — no chat token
# ---------------------------------------------------------------------------


class TestSeekContinuationOnly:
    """seek_continuation_only.json — playerSeekContinuationData only."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(
            _load("seek_continuation_only")
        )

    def test_next_continuation_is_none(self) -> None:
        # Seek continuations are not chat tokens; result is end-of-stream.
        assert self.result.next_continuation is None

    def test_is_end_true(self) -> None:
        assert self.result.is_end is True

    def test_actions_empty(self) -> None:
        assert self.result.actions == []

    def test_timeout_ms_is_none(self) -> None:
        assert self.result.timeout_ms is None


# ---------------------------------------------------------------------------
# Live heartbeat — no actions, has continuation token
# ---------------------------------------------------------------------------


class TestNoActionsLiveHeartbeat:
    """no_actions_live_heartbeat.json — continuation token but no actions."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(
            _load("no_actions_live_heartbeat")
        )

    def test_actions_empty(self) -> None:
        assert self.result.actions == []

    def test_next_continuation_exact_value(self) -> None:
        assert self.result.next_continuation == "HEARTBEAT_TOKEN_NEXT"

    def test_is_end_false(self) -> None:
        assert self.result.is_end is False

    def test_timeout_ms(self) -> None:
        assert self.result.timeout_ms == 5000


# ---------------------------------------------------------------------------
# Timeout extraction — raw timeout > 8000 ms is preserved for poll policy
# ---------------------------------------------------------------------------


class TestTimeoutClampingLarge:
    """timeout_clamping_large.json — raw timeoutMs=20000 is extracted."""

    def setup_method(self) -> None:
        self.result = parse_continuation_response(
            _load("timeout_clamping_large")
        )

    def test_timeout_ms_exact_value(self) -> None:
        assert self.result.timeout_ms == 20000

    def test_next_continuation_exact_value(self) -> None:
        assert self.result.next_continuation == "CLAMPED_TIMEOUT_TOKEN"

    def test_is_end_false(self) -> None:
        assert self.result.is_end is False


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
        assert result.is_end is True
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

# SPDX-License-Identifier: MIT

"""Unit tests for continuation request errors and malformed payloads."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    ParsingError,
    RetriesExceeded,
)
from chat_downloader.sites.youtube.client_requests_errors import (
    _apply_retry_or_raise,
    _contains_challenge_text,
    _handle_http_error,
    _handle_json_api_error,
    _is_retryable_status,
)
from chat_downloader.sites.youtube.client_requests_initial import (
    _contains_challenge_text as _initial_contains_challenge_text,
)
from chat_downloader.sites.youtube.client_requests_initial import (
    _get_initial_info,
)
from chat_downloader.sites.youtube.continuations import _extract_next_continuation
from chat_downloader.utils.retry_utils import RetryPolicy


@pytest.fixture
def policy():
    return RetryPolicy(max_attempts=3, retry_timeout=0, interruptible_retry=False)


def _make_response(status_code=200, json_body=None, text=""):
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        json=lambda: {} if json_body is None else json_body,
    )


@pytest.mark.parametrize(
    "response",
    [
        _make_response(403, {"error": {"message": "captcha required"}}),
        _make_response(503, text="Please verify you are human to continue."),
    ],
)
def test_http_challenge_raises(response, policy) -> None:
    with pytest.raises(CaptchaChallengeRequired):
        _handle_http_error(response, "https://example.com/", 1, 3, policy)


def test_json_challenge_raises(policy) -> None:
    with pytest.raises(CaptchaChallengeRequired):
        _handle_json_api_error(
            {"code": 403, "message": "recaptcha challenge required"},
            "https://example.com/",
            1,
            3,
            policy,
        )


@pytest.mark.parametrize(
    ("handler", "payload"),
    [
        (_handle_http_error, _make_response(429)),
        (_handle_json_api_error, {"code": 429, "message": "Rate limit exceeded"}),
        (_handle_json_api_error, {"code": 0, "message": "Unknown error occurred"}),
    ],
)
def test_retryable_error_waits_and_continues(handler, payload) -> None:
    policy = MagicMock(spec=RetryPolicy)
    policy.can_retry.return_value = True
    assert handler(payload, "https://example.com/", 1, 3, policy) is True
    policy.wait.assert_called_once()


def test_http_error_exhausts_retries(policy) -> None:
    with pytest.raises(RetriesExceeded):
        _handle_http_error(_make_response(500), "https://example.com/", 3, 3, policy)


def test_terminal_http_error_returns_false(policy) -> None:
    assert (
        _handle_http_error(_make_response(400), "https://example.com/", 1, 3, policy)
        is False
    )


def test_retry_wait_is_not_interruptible() -> None:
    policy = MagicMock(spec=RetryPolicy)
    policy.can_retry.return_value = True
    _apply_retry_or_raise(
        policy, 1, "https://example.com/", "some error", RetriesExceeded
    )
    policy.wait.assert_called_once_with(1, interruptible=False)


def test_exhausted_retry_error_includes_reason_and_url(policy) -> None:
    with pytest.raises(RetriesExceeded) as exc:
        _apply_retry_or_raise(
            policy, 3, "https://my.endpoint/chat", "some error", RetriesExceeded
        )
    assert "some error" in str(exc.value)
    assert "https://my.endpoint/chat" in str(exc.value)


@pytest.mark.parametrize("value", [None, 42, []])
@pytest.mark.parametrize(
    "contains_challenge", [_contains_challenge_text, _initial_contains_challenge_text]
)
def test_challenge_detection_ignores_non_strings(contains_challenge, value) -> None:
    assert contains_challenge(value) is False


@pytest.mark.parametrize("value", [None, "403"])
def test_retryable_status_ignores_non_integers(value) -> None:
    assert _is_retryable_status(value) is False


@pytest.mark.parametrize("entry", [{}, "not-a-dict", {"someKey": "not_a_dict"}])
def test_extract_next_continuation_skips_malformed_entries(entry) -> None:
    assert _extract_next_continuation({"continuations": [entry]}) == (
        None,
        None,
        None,
        {},
    )


def test_get_initial_info_raises_parsing_error_when_html_unparseable() -> None:
    response = SimpleNamespace(text="<html>nothing useful</html>", status_code=200)
    with pytest.raises(ParsingError, match="Unable to parse initial video data"):
        _get_initial_info(
            url="https://www.youtube.com/watch?v=abc",
            session_get=MagicMock(return_value=response),
            params=None,
            yt_initial_data_re=r"ytInitialData\s*=\s*({.*?});",
            yt_cfg_re=r"ytcfg\.set\(({.*?})\);",
            yt_initial_player_response_re=r"ytInitialPlayerResponse\s*=\s*({.*?});",
        )

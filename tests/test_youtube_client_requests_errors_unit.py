# SPDX-License-Identifier: MIT

"""Seam tests for the YouTube HTTP/JSON error-handler cluster."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.sites.youtube.client_requests_errors import (
    _handle_http_error,
    _handle_json_api_error,
    _handle_missing_live_chat_continuation,
)
from chat_downloader.utils.retry_utils import RetryPolicy

_POLICY_CAN_RETRY = RetryPolicy(
    max_attempts=2, retry_timeout=0.0, interruptible_retry=False
)
_POLICY_EXHAUSTED = RetryPolicy(
    max_attempts=1, retry_timeout=0.0, interruptible_retry=False
)

_URL = "http://example.com/api"


def _fake_response(
    status_code: int,
    body: dict | None = None,
    text: str = "",
) -> object:
    """Return a minimal fake HTTP response."""
    from json.decoder import JSONDecodeError

    def _json() -> dict:
        if body is None:
            raise JSONDecodeError("no body", "", 0)
        return body

    return SimpleNamespace(status_code=status_code, text=text, json=_json)


# ── _handle_http_error ────────────────────────────────────────────────────────


def test_handle_http_error_429_retry_budget_remains() -> None:
    resp = _fake_response(429)
    assert _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY) is True


def test_handle_http_error_403_budget_exhausted_raises() -> None:
    resp = _fake_response(403)
    with pytest.raises(RetriesExceeded):
        _handle_http_error(resp, _URL, 1, 1, _POLICY_EXHAUSTED)


def test_handle_http_error_5xx_is_retryable() -> None:
    resp = _fake_response(503)
    assert _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY) is True


def test_handle_http_error_challenge_body_raises_captcha() -> None:
    resp = _fake_response(403, text="please verify you are human")
    with pytest.raises(CaptchaChallengeRequired, match="captcha"):
        _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY)


def test_handle_http_error_challenge_yt_msg_raises_captcha() -> None:
    body = {"error": {"message": "unusual traffic detected"}}
    resp = _fake_response(400, body=body)
    with pytest.raises(CaptchaChallengeRequired):
        _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY)


def test_handle_http_error_non_retryable_returns_false() -> None:
    resp = _fake_response(400)
    assert _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY) is False


def test_handle_http_error_404_returns_false() -> None:
    resp = _fake_response(404)
    assert _handle_http_error(resp, _URL, 1, 2, _POLICY_CAN_RETRY) is False


# ── _handle_json_api_error ────────────────────────────────────────────────────


def test_handle_json_api_error_challenge_raises_captcha() -> None:
    error: dict = {"code": 400, "message": "verify you are human"}
    with pytest.raises(CaptchaChallengeRequired):
        _handle_json_api_error(error, _URL, 1, 2, _POLICY_CAN_RETRY)


def test_handle_json_api_error_403_retry_budget_remains() -> None:
    error: dict = {"code": 403, "message": "Forbidden"}
    assert _handle_json_api_error(error, _URL, 1, 2, _POLICY_CAN_RETRY) is True


def test_handle_json_api_error_429_budget_exhausted_raises() -> None:
    error: dict = {"code": 429, "message": "Too Many Requests"}
    with pytest.raises(RetriesExceeded):
        _handle_json_api_error(error, _URL, 1, 1, _POLICY_EXHAUSTED)


def test_handle_json_api_error_unknown_retry_budget_remains() -> None:
    error: dict = {"code": 400, "message": "Unknown error occurred"}
    assert _handle_json_api_error(error, _URL, 1, 2, _POLICY_CAN_RETRY) is True


def test_handle_json_api_error_unknown_budget_exhausted_raises() -> None:
    error: dict = {"code": 400, "message": "Unknown error occurred"}
    with pytest.raises(IncompleteContinuationError):
        _handle_json_api_error(error, _URL, 1, 1, _POLICY_EXHAUSTED)


def test_handle_json_api_error_unrecognised_returns_false() -> None:
    error: dict = {"code": 400, "message": "Something went wrong"}
    assert _handle_json_api_error(error, _URL, 1, 2, _POLICY_CAN_RETRY) is False


def test_handle_json_api_error_empty_dict_returns_false() -> None:
    assert _handle_json_api_error({}, _URL, 1, 2, _POLICY_CAN_RETRY) is False


# ── _handle_missing_live_chat_continuation ────────────────────────────────────


def test_missing_live_chat_guard_disabled_returns_false() -> None:
    result = _handle_missing_live_chat_continuation(
        {"continuationContents": {}},
        require_live_chat_continuation=False,
        error=None,
        attempt_number=1,
        max_attempts=2,
        retry_policy=_POLICY_CAN_RETRY,
        continuation_url=_URL,
    )
    assert result is False


def test_missing_live_chat_error_present_returns_false() -> None:
    result = _handle_missing_live_chat_continuation(
        {"continuationContents": {}},
        require_live_chat_continuation=True,
        error=ValueError("already handled"),
        attempt_number=1,
        max_attempts=2,
        retry_policy=_POLICY_CAN_RETRY,
        continuation_url=_URL,
    )
    assert result is False


def test_missing_live_chat_empty_response_returns_false() -> None:
    result = _handle_missing_live_chat_continuation(
        {},
        require_live_chat_continuation=True,
        error=None,
        attempt_number=1,
        max_attempts=2,
        retry_policy=_POLICY_CAN_RETRY,
        continuation_url=_URL,
    )
    assert result is False


def test_missing_live_chat_block_present_returns_false() -> None:
    result = _handle_missing_live_chat_continuation(
        {"continuationContents": {"liveChatContinuation": {"actions": []}}},
        require_live_chat_continuation=True,
        error=None,
        attempt_number=1,
        max_attempts=2,
        retry_policy=_POLICY_CAN_RETRY,
        continuation_url=_URL,
    )
    assert result is False


def test_missing_live_chat_absent_can_retry() -> None:
    result = _handle_missing_live_chat_continuation(
        {"continuationContents": {"otherContinuation": {}}},
        require_live_chat_continuation=True,
        error=None,
        attempt_number=1,
        max_attempts=2,
        retry_policy=_POLICY_CAN_RETRY,
        continuation_url=_URL,
    )
    assert result is True


def test_missing_live_chat_absent_exhausted_raises() -> None:
    with pytest.raises(IncompleteContinuationError):
        _handle_missing_live_chat_continuation(
            {"continuationContents": {"otherContinuation": {}}},
            require_live_chat_continuation=True,
            error=None,
            attempt_number=1,
            max_attempts=1,
            retry_policy=_POLICY_EXHAUSTED,
            continuation_url=_URL,
        )

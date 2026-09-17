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


@pytest.mark.parametrize(
    ("status", "expected"), [(429, True), (503, True), (400, False), (404, False)]
)
def test_handle_http_error_routes_status(status, expected) -> None:
    assert (
        _handle_http_error(
            _fake_response(status),
            _URL,
            1,
            2,
            _POLICY_CAN_RETRY,
        )
        is expected
    )


def test_handle_http_error_403_budget_exhausted_raises() -> None:
    with pytest.raises(RetriesExceeded):
        _handle_http_error(_fake_response(403), _URL, 1, 1, _POLICY_EXHAUSTED)


@pytest.mark.parametrize(
    "response",
    [
        _fake_response(403, text="please verify you are human"),
        _fake_response(400, body={"error": {"message": "unusual traffic detected"}}),
    ],
)
def test_handle_http_error_challenge_raises_captcha(response) -> None:
    with pytest.raises(CaptchaChallengeRequired):
        _handle_http_error(response, _URL, 1, 2, _POLICY_CAN_RETRY)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": 403, "message": "Forbidden"}, True),
        ({"code": 400, "message": "Unknown error occurred"}, True),
        ({"code": 400, "message": "Something went wrong"}, False),
        ({}, False),
    ],
)
def test_handle_json_api_error_routes_response(error, expected) -> None:
    assert _handle_json_api_error(error, _URL, 1, 2, _POLICY_CAN_RETRY) is expected


@pytest.mark.parametrize(
    ("error", "exception"),
    [
        ({"code": 400, "message": "verify you are human"}, CaptchaChallengeRequired),
        ({"code": 429, "message": "Too Many Requests"}, RetriesExceeded),
        (
            {"code": 400, "message": "Unknown error occurred"},
            IncompleteContinuationError,
        ),
    ],
)
def test_handle_json_api_error_raises(error, exception) -> None:
    with pytest.raises(exception):
        _handle_json_api_error(error, _URL, 1, 1, _POLICY_EXHAUSTED)


def _missing_continuation(body, *, required=True, error=None, attempts=2):
    return _handle_missing_live_chat_continuation(
        body,
        require_live_chat_continuation=required,
        error=error,
        attempt_number=1,
        max_attempts=attempts,
        retry_policy=_POLICY_CAN_RETRY if attempts == 2 else _POLICY_EXHAUSTED,
        continuation_url=_URL,
    )


@pytest.mark.parametrize(
    ("body", "kwargs", "expected"),
    [
        ({"continuationContents": {}}, {"required": False}, False),
        ({"continuationContents": {}}, {"error": ValueError("already handled")}, False),
        ({}, {}, False),
        (
            {"continuationContents": {"liveChatContinuation": {"actions": []}}},
            {},
            False,
        ),
        ({"continuationContents": {"otherContinuation": {}}}, {}, True),
    ],
)
def test_missing_live_chat_guard(body, kwargs, expected) -> None:
    assert _missing_continuation(body, **kwargs) is expected


def test_missing_live_chat_absent_exhausted_raises() -> None:
    with pytest.raises(IncompleteContinuationError):
        _missing_continuation(
            {"continuationContents": {"otherContinuation": {}}},
            attempts=1,
        )

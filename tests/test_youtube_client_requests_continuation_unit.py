# SPDX-License-Identifier: MIT

"""Isolated unit tests for client_requests_continuation pure helpers."""

from __future__ import annotations

import pytest

from chat_downloader.errors import (
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.sites.youtube.client_requests_errors import (
    _apply_retry_or_raise,
    _captcha_guidance_message,
    _contains_challenge_text,
    _is_retryable_status,
    _retry_or_raise_exhausted,
    _retry_or_raise_incomplete,
)
from chat_downloader.utils.retry_utils import RetryPolicy

# RetryPolicy with two attempts and zero-second waits avoids real sleeping.
_POLICY_CAN_RETRY = RetryPolicy(
    max_attempts=2, retry_timeout=0.0, interruptible_retry=False
)
_POLICY_EXHAUSTED = RetryPolicy(
    max_attempts=1, retry_timeout=0.0, interruptible_retry=False
)


# ── _contains_challenge_text ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("contains captcha text", True),
        ("verify you are human now", True),
        ("unusual traffic detected", True),
        ("recaptcha required", True),
        ("challenge response needed", True),
        ("CAPTCHA REQUIRED", True),  # case-insensitive
        ("VERIFY YOU ARE HUMAN", True),
        ("normal response text", False),
        ("", False),
        (123, False),  # non-string
        (None, False),
        ([], False),
    ],
)
def test_contains_challenge_text(text: object, expected: bool) -> None:
    assert _contains_challenge_text(text) == expected


# ── _captcha_guidance_message ─────────────────────────────────────────────────


def test_captcha_guidance_message_includes_endpoint_and_detail() -> None:
    msg = _captcha_guidance_message(
        endpoint="https://example.com/api", detail="HTTP 429: rate limited"
    )
    assert "https://example.com/api" in msg
    assert "HTTP 429: rate limited" in msg


# ── _is_retryable_status ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (403, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (599, True),  # 5xx edge
        (400, False),
        (404, False),
        (200, False),
        (None, False),
        ("403", False),  # non-int
        (403.0, False),  # float
    ],
)
def test_is_retryable_status(code: object, expected: bool) -> None:
    assert _is_retryable_status(code) == expected  # type: ignore[arg-type]


# ── _apply_retry_or_raise ─────────────────────────────────────────────────────


def test_apply_retry_or_raise_returns_when_retry_is_allowed() -> None:
    # attempt 1 with max 2 → can_retry → should return without raising
    _apply_retry_or_raise(
        _POLICY_CAN_RETRY, 1, "http://url", "some error", RetriesExceeded
    )


def test_apply_retry_or_raise_raises_when_budget_exhausted() -> None:
    with pytest.raises(RetriesExceeded, match="Retries exhausted"):
        _apply_retry_or_raise(
            _POLICY_EXHAUSTED, 1, "http://url", "some error", RetriesExceeded
        )


def test_apply_retry_or_raise_propagates_custom_exc_class() -> None:
    with pytest.raises(ValueError, match="Retries exhausted"):
        _apply_retry_or_raise(
            _POLICY_EXHAUSTED, 1, "http://url", "msg", ValueError
        )


def test_apply_retry_or_raise_error_message_includes_url() -> None:
    with pytest.raises(RetriesExceeded, match="http://url"):
        _apply_retry_or_raise(
            _POLICY_EXHAUSTED, 1, "http://url", "err", RetriesExceeded
        )


# ── _retry_or_raise_exhausted ─────────────────────────────────────────────────


def test_retry_or_raise_exhausted_returns_true_when_retry_allowed() -> None:
    result = _retry_or_raise_exhausted(
        1, 2, _POLICY_CAN_RETRY, "http://url", "err", "test-label"
    )
    assert result is True


def test_retry_or_raise_exhausted_raises_when_budget_gone() -> None:
    with pytest.raises(RetriesExceeded):
        _retry_or_raise_exhausted(
            1, 1, _POLICY_EXHAUSTED, "http://url", "err", "test-label"
        )


def test_retry_or_raise_exhausted_error_message_includes_url() -> None:
    with pytest.raises(RetriesExceeded, match="http://url"):
        _retry_or_raise_exhausted(
            1, 1, _POLICY_EXHAUSTED, "http://url", "err", "test-label"
        )


# ── _retry_or_raise_incomplete ────────────────────────────────────────────────


def test_retry_or_raise_incomplete_returns_when_retry_allowed() -> None:
    # Should return without raising
    _retry_or_raise_incomplete(1, "reason", 2, _POLICY_CAN_RETRY, "http://url")


def test_retry_or_raise_incomplete_raises_on_budget_exhausted() -> None:
    with pytest.raises(IncompleteContinuationError):
        _retry_or_raise_incomplete(
            1, "reason", 1, _POLICY_EXHAUSTED, "http://url"
        )


def test_retry_or_raise_incomplete_error_message_includes_endpoint() -> None:
    with pytest.raises(IncompleteContinuationError, match="http://url"):
        _retry_or_raise_incomplete(
            1, "reason", 1, _POLICY_EXHAUSTED, "http://url"
        )

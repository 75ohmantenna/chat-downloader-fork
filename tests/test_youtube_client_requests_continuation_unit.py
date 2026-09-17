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
)
from chat_downloader.utils.retry_utils import RetryPolicy


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


def test_captcha_guidance_message_includes_endpoint_and_detail() -> None:
    msg = _captcha_guidance_message(
        endpoint="https://example.com/api", detail="HTTP 429: rate limited"
    )
    assert "https://example.com/api" in msg
    assert "HTTP 429: rate limited" in msg


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


@pytest.mark.parametrize("exc_cls", [RetriesExceeded, ValueError])
def test_apply_retry_or_raise_preserves_error_context(exc_cls) -> None:
    policy = RetryPolicy(max_attempts=1, retry_timeout=0)
    with pytest.raises(exc_cls, match="http://url") as caught:
        _apply_retry_or_raise(policy, 1, "http://url", "network failed", exc_cls)
    assert "network failed" in str(caught.value)


@pytest.mark.parametrize("exc_cls", [RetriesExceeded, IncompleteContinuationError])
def test_retry_or_raise_exhausted_preserves_error_context(exc_cls) -> None:
    policy = RetryPolicy(max_attempts=1, retry_timeout=0)
    with pytest.raises(exc_cls, match="http://url") as caught:
        _retry_or_raise_exhausted(
            1,
            1,
            policy,
            "http://url",
            "reason",
            "test-label",
            exc_cls=exc_cls,
        )
    assert "reason" in str(caught.value)


@pytest.mark.parametrize("incomplete", [False, True])
def test_retry_helpers_wait_before_retrying(monkeypatch, incomplete) -> None:
    waits = []
    monkeypatch.setattr(
        RetryPolicy,
        "wait",
        lambda self, attempt, **kwargs: waits.append((attempt, kwargs)),
    )
    policy = RetryPolicy(max_attempts=2, retry_timeout=0)
    if incomplete:
        assert (
            _retry_or_raise_exhausted(
                1,
                2,
                policy,
                "http://url",
                "reason",
                "incomplete continuation response",
                exc_cls=IncompleteContinuationError,
            )
            is True
        )
    else:
        _apply_retry_or_raise(policy, 1, "http://url", "reason", RetriesExceeded)
    assert waits == [(1, {"interruptible": False})]

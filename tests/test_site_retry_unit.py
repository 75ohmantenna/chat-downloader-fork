# SPDX-License-Identifier: MIT
from __future__ import annotations

from json import JSONDecodeError

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites import retry as site_retry


@pytest.fixture
def retry_events(monkeypatch):
    logs, sleeps = [], []
    monkeypatch.setattr(
        site_retry, "log", lambda level, value: logs.append((level, value))
    )
    monkeypatch.setattr(site_retry, "polling_sleep", sleeps.append)
    return logs, sleeps


@pytest.mark.parametrize(
    ("text", "attempt", "limit", "typed"),
    [("prefix", 1, 3, False), (("prefix",), 1, 2, False), (None, 2, 4, True)],
)
def test_retry_text_and_typed_policy(retry_events, text, attempt, limit, typed):
    policy = {"max_attempts": limit, "retry_timeout": 0, "interruptible_retry": False}
    options = (
        {"request": ChatRequest(url="https://example.invalid/watch?v=1", **policy)}
        if typed
        else policy
    )
    site_retry.retry(
        attempt_number=attempt, error=ConnectionError("offline"), text=text, **options
    )
    logs, sleeps = retry_events
    prefix = ["prefix"] if text else []
    assert logs == [
        (
            "warning",
            [
                *prefix,
                f"Retry #{attempt}/{limit} (sleep for 0.0s). offline (ConnectionError)",
            ],
        )
    ]
    assert sleeps == [0.0]


@pytest.mark.parametrize("title", ["Example page", ""])
def test_retry_json_decode_context_and_optional_title(retry_events, title):
    html = f"<title>{title}</title>" if title else "<html></html>"
    error = JSONDecodeError("bad json", html, 4 if title else 0)
    site_retry.retry(
        attempt_number=1,
        max_attempts=2,
        error=error,
        retry_timeout=0,
        text=["prefix"] if title else None,
        interruptible_retry=False,
    )
    logs, sleeps = retry_events
    expected = [("debug", f"JSONDecodeError at pos={error.pos!r}: {error.msg!r}")]
    if title:
        expected.append(("debug", f"Title: {title}"))
    expected.append(
        (
            "warning",
            (["prefix"] if title else [])
            + [f"Retry #1/2 (sleep for 0.0s). {error} (JSONDecodeError)"],
        )
    )
    assert logs == expected
    assert sleeps == [0.0]


def test_attempt_numbers_rejects_non_positive_limits():
    with pytest.raises(RetriesExceeded):
        site_retry._attempt_numbers(0)


def test_attempt_numbers_returns_1_indexed_range():
    assert list(site_retry._attempt_numbers(3)) == [1, 2, 3]

# SPDX-License-Identifier: MIT

from __future__ import annotations

from json import JSONDecodeError

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites import retry as site_retry


def test_retry_wraps_scalar_text_and_uses_polling_sleep(
    monkeypatch,
) -> None:
    logs = []
    sleeps = []

    monkeypatch.setattr(
        site_retry,
        "log",
        lambda level, value: logs.append((level, value)),
    )
    monkeypatch.setattr(
        site_retry,
        "polling_sleep",
        sleeps.append,
    )

    site_retry.retry(
        attempt_number=1,
        max_attempts=3,
        error=ConnectionError("offline"),
        retry_timeout=0,
        text="prefix",
        interruptible_retry=False,
    )

    assert logs == [
        (
            "warning",
            [
                "prefix",
                "Retry #1/3 (sleep for 0.0s). offline (ConnectionError)",
            ],
        ),
    ]
    assert sleeps == [0.0]


def test_retry_logs_json_decode_context_and_page_title(monkeypatch) -> None:
    logs = []
    sleeps = []
    error = JSONDecodeError("bad json", "<title>Example page</title>", 4)

    monkeypatch.setattr(
        site_retry,
        "log",
        lambda level, value: logs.append((level, value)),
    )
    monkeypatch.setattr(
        site_retry,
        "polling_sleep",
        sleeps.append,
    )
    monkeypatch.setattr(
        site_retry, "get_title_of_webpage", lambda html: "Example page"
    )

    site_retry.retry(
        attempt_number=1,
        max_attempts=2,
        error=error,
        retry_timeout=0,
        text=["prefix"],
        interruptible_retry=False,
    )

    assert logs[0] == (
        "debug",
        f"JSONDecodeError at pos={error.pos!r}: {error.msg!r}",
    )
    assert logs[1] == ("debug", "Title: Example page")
    assert logs[2] == (
        "warning",
        [
            "prefix",
            "Retry #1/2 (sleep for 0.0s). bad json: line 1 column 5 (char 4) (JSONDecodeError)",  # noqa: E501
        ],
    )
    assert sleeps == [0.0]


def test_retry_skips_page_title_log_when_title_missing(monkeypatch) -> None:
    logs = []

    monkeypatch.setattr(
        site_retry,
        "log",
        lambda level, value: logs.append((level, value)),
    )
    monkeypatch.setattr(site_retry, "polling_sleep", lambda _seconds: None)
    monkeypatch.setattr(site_retry, "get_title_of_webpage", lambda _html: "")

    site_retry.retry(
        attempt_number=1,
        max_attempts=2,
        error=JSONDecodeError("bad json", "<html></html>", 0),
        retry_timeout=0,
        interruptible_retry=False,
    )

    assert logs == [
        ("debug", "JSONDecodeError at pos=0: 'bad json'"),
        (
            "warning",
            [
                "Retry #1/2 (sleep for 0.0s). bad json: line 1 column 1 (char 0) (JSONDecodeError)",  # noqa: E501
            ],
        ),
    ]


def test_retry_can_read_policy_from_typed_request(monkeypatch) -> None:
    logs = []
    sleeps = []
    request = ChatRequest(
        url="https://example.invalid/watch?v=1",
        max_attempts=4,
        retry_timeout=0,
        interruptible_retry=False,
    )

    monkeypatch.setattr(
        site_retry,
        "log",
        lambda level, value: logs.append((level, value)),
    )
    monkeypatch.setattr(
        site_retry,
        "polling_sleep",
        sleeps.append,
    )

    site_retry.retry(
        attempt_number=2,
        error=ConnectionError("offline"),
        request=request,
    )

    assert logs == [
        (
            "warning",
            ["Retry #2/4 (sleep for 0.0s). offline (ConnectionError)"],
        ),
    ]
    assert sleeps == [0.0]


def test_attempt_numbers_rejects_non_positive_limits() -> None:
    with pytest.raises(RetriesExceeded):
        site_retry._attempt_numbers(0)


def test_attempt_numbers_returns_1_indexed_range() -> None:
    assert list(site_retry._attempt_numbers(3)) == [1, 2, 3]


def test_retry_text_inputs_support_tuple_payload(monkeypatch) -> None:
    logs = []
    sleeps = []

    monkeypatch.setattr(
        site_retry,
        "log",
        lambda level, value: logs.append((level, value)),
    )
    monkeypatch.setattr(site_retry, "polling_sleep", sleeps.append)

    site_retry.retry(
        attempt_number=1,
        max_attempts=2,
        error=ConnectionError("offline"),
        retry_timeout=0,
        text=("prefix",),
        interruptible_retry=False,
    )

    assert logs == [
        (
            "warning",
            [
                "prefix",
                "Retry #1/2 (sleep for 0.0s). offline (ConnectionError)",
            ],
        ),
    ]
    assert sleeps == [0.0]

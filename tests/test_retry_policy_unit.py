# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.utils.retry_utils import RetryPolicy


def test_negative_retry_timeout_uses_manual_pause(monkeypatch) -> None:
    pause = Mock()
    timed_input = Mock()

    monkeypatch.setattr("chat_downloader.utils.retry_utils.pause", pause)
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils.timed_input", timed_input
    )

    policy = RetryPolicy(
        max_attempts=3, retry_timeout=-1, interruptible_retry=True
    )
    policy.wait(1)

    pause.assert_called_once_with()
    timed_input.assert_not_called()


def test_negative_retry_timeout_sleep_text_mentions_manual_continue() -> None:
    policy = RetryPolicy(
        max_attempts=3, retry_timeout=-1, interruptible_retry=True
    )
    assert policy.sleep_text(1) == "(press Enter to continue)"


def test_retry_policy_can_retry_and_non_interruptible_wait(monkeypatch) -> None:
    sleep = Mock()
    monkeypatch.setattr("chat_downloader.utils.retry_utils.time.sleep", sleep)

    policy = RetryPolicy(
        max_attempts=3, retry_timeout=2, interruptible_retry=False
    )

    assert policy.can_retry(2) is True
    assert policy.can_retry(3) is False
    assert policy.sleep_text(1) == "(sleep for 2.0s)"

    policy.wait(1)

    sleep.assert_called_once_with(2)


def test_retry_policy_non_numeric_timeout_returns_empty_sleep_text() -> None:
    policy = RetryPolicy(max_attempts=2, retry_timeout="later")

    assert policy.sleep_seconds(1) is None
    assert policy.sleep_text(1) == ""


def test_wait_uses_explicit_interruptible_override(monkeypatch) -> None:
    timed_input = Mock()
    sleep = Mock()
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils.timed_input", timed_input
    )

    policy = RetryPolicy(
        max_attempts=3, retry_timeout=1, interruptible_retry=False
    )
    policy.wait(1, interruptible=True, sleep_func=sleep)

    timed_input.assert_called_once_with(1.0)
    sleep.assert_not_called()


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_sleep_text_respects_interruptible_override(attempt) -> None:
    policy = RetryPolicy(
        max_attempts=3, retry_timeout=1, interruptible_retry=False
    )
    assert (
        policy.sleep_text(attempt, interruptible=True)
        == "(sleep for 1.0s or press Enter)"
    )

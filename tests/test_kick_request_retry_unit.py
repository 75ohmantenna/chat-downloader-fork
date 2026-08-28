# SPDX-License-Identifier: MIT

"""Tests for the shared Kick service-request retry policy."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import KickError, request_retry
from chat_downloader.sites.kick.errors import KickServerError


def test_fetch_with_retry_recovers_from_temporary_failure() -> None:
    fetch = Mock(side_effect=[OSError("timeout"), {"ok": True}])
    request = ChatRequest(
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
    )

    assert request_retry.fetch_with_retry(fetch, request) == {"ok": True}
    assert fetch.call_count == 2


def test_fetch_with_retry_exhausts_transient_failures() -> None:
    fetch = Mock(side_effect=KickServerError("rate limited"))
    request = ChatRequest(
        max_attempts=2,
        retry_timeout=0,
        interruptible_retry=False,
    )

    with pytest.raises(RetriesExceeded):
        request_retry.fetch_with_retry(fetch, request)

    assert fetch.call_count == 2


def test_fetch_with_retry_does_not_retry_terminal_failure() -> None:
    fetch = Mock(side_effect=KickError("not found"))
    request = ChatRequest(max_attempts=3, retry_timeout=0)

    with pytest.raises(KickError, match="not found"):
        request_retry.fetch_with_retry(fetch, request)

    fetch.assert_called_once()

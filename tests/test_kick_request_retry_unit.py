# SPDX-License-Identifier: MIT
from __future__ import annotations

from threading import Event
from unittest.mock import Mock

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.sites.kick import request_retry
from chat_downloader.sites.kick.errors import KickCountryBlocked, KickServerError
from chat_downloader.utils.timed_generator import _PollingCancelled
from tests.kick_helpers import request


@pytest.mark.parametrize(
    ("effects", "error", "attempts", "calls"),
    [
        ([OSError("timeout"), {"ok": True}], None, 2, 2),
        (KickServerError("rate limited"), RetriesExceeded, 2, 2),
        (KickCountryBlocked("country blocked"), KickCountryBlocked, 3, 1),
    ],
)
def test_fetch_retry_outcomes(effects, error, attempts, calls):
    fetch = Mock(side_effect=effects)
    options = request(max_attempts=attempts)
    if error:
        with pytest.raises(error):
            request_retry.fetch_with_retry(fetch, options)
    else:
        assert request_retry.fetch_with_retry(fetch, options) == {"ok": True}
    assert fetch.call_count == calls


def test_cancelled_replay_request_does_not_start_an_attempt():
    cancelled = Event()
    cancelled.set()
    fetch = Mock()
    with pytest.raises(_PollingCancelled):
        request_retry.fetch_with_retry(fetch, request(), cancelled=cancelled)
    fetch.assert_not_called()

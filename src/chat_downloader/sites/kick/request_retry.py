# SPDX-License-Identifier: MIT

"""Retry policy for transient Kick service requests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.errors import RetriesExceeded
from chat_downloader.sites.retry import retry

from .errors import KickServerError

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest


def fetch_with_retry[T](fetch: Callable[[], T], request: ChatRequest) -> T:
    """Run a transient Kick request with the configured retry policy."""
    for attempt_number in range(1, request.max_attempts + 1):
        try:
            return fetch()
        except (KickServerError, RequestException, OSError) as error:
            retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover

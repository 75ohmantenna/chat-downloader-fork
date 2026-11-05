# SPDX-License-Identifier: MIT

"""Bound decoded history bodies before full buffering and JSON parsing.

Requests/cloudscraper use a response hook with streamed, decoded chunks; the
hook runs before cloudscraper can inspect the body. curl-cffi's stream producer
uses an unbounded queue, so its synchronous native content callback is used
instead. Callback overflow is latched, not raised through the native callback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from .errors import KickResourceLimit
from .resource_limits import MAX_HISTORY_RESPONSE_BYTES

if TYPE_CHECKING:
    from .http_session import _KickSession

_HTTP_CHUNK_BYTES = 16 * 1024


class _HistoryBody:
    """Retain at most the finite decompressed history response ceiling."""

    def __init__(self) -> None:
        """Initialize one bounded collection, independent of backend buffering."""
        self.content = bytearray()
        self.exceeded = False

    def collect(self, chunk: bytes) -> int:
        """Return zero on overflow so libcurl aborts without callback exceptions."""
        if self.exceeded:
            return 0
        remaining = MAX_HISTORY_RESPONSE_BYTES - len(self.content)
        if len(chunk) > remaining:
            self.content.extend(chunk[:remaining])
            self.exceeded = True
            return 0
        self.content.extend(chunk)
        return len(chunk)


def _consume_response(response: requests.Response, body: _HistoryBody) -> None:
    """Read bounded decoded chunks, always releasing the backend response."""
    try:
        if 300 <= response.status_code < 400 or response.status_code == 423:
            return
        for chunk in response.iter_content(chunk_size=_HTTP_CHUNK_BYTES):
            if chunk and not body.collect(chunk):
                break
    finally:
        response.close()


def _buffered_response(
    original: requests.Response, body: _HistoryBody
) -> requests.Response:
    """Expose only the bounded bytes to the existing status/challenge policy."""
    response = requests.Response()
    response.status_code = original.status_code
    response.headers.update(original.headers)
    response.encoding = "utf-8"
    response._content = bytes(body.content)
    response._content_consumed = True
    return response


def fetch_history_response(
    session: _KickSession, url: str, options: dict[str, object]
) -> tuple[requests.Response, bool]:
    """Fetch a finite history body with the backend's pre-buffering boundary."""
    body = _HistoryBody()
    kwargs = {**options, "stream": True}
    # This recognizes subclasses without importing an optional backend eagerly.
    if any(cls.__module__.startswith("curl_cffi.") for cls in type(session).__mro__):
        response = _fetch_curl_history(session, url, kwargs, body)
        response = _buffered_response(response, body)
    else:
        response = _fetch_streamed_history(session, url, kwargs, body)
    return response, body.exceeded


def _fetch_streamed_history(
    session: _KickSession,
    url: str,
    kwargs: dict[str, object],
    body: _HistoryBody,
) -> requests.Response:
    consumed = False

    def bound_response(
        response: requests.Response, **_kwargs: object
    ) -> requests.Response:
        nonlocal consumed
        if body.exceeded:
            response.close()
        else:
            # A scraper may issue another bounded response while solving access.
            body.content.clear()
            _consume_response(response, body)
        consumed = True
        # Cloudscraper may inspect this body immediately after requests hooks run.
        # Retain the real response/raw headers for requests' cookie extraction.
        response._content = bytes(body.content)
        response._content_consumed = True
        response.encoding = "utf-8"
        return response

    if requests.Session in type(session).__mro__:
        kwargs["hooks"] = {"response": [bound_response]}
    response = session.get(url, **kwargs)
    if not consumed:
        _consume_response(response, body)
        response = _buffered_response(response, body)
    return response


def _fetch_curl_history(
    session: _KickSession,
    url: str,
    kwargs: dict[str, object],
    body: _HistoryBody,
) -> requests.Response:
    # stream=True would enqueue arbitrary data before iter_content consumes it.
    kwargs.update(stream=False, content_callback=body.collect)
    try:
        response = session.get(url, **kwargs)
    except Exception as error:
        wrapped_response = getattr(error, "response", None)
        if not body.exceeded:
            raise
        if wrapped_response is None:
            msg = (
                "Kick history response byte resource limit exceeded; "
                "capture is incomplete."
            )
            raise KickResourceLimit(msg) from None
        response = wrapped_response
    return response

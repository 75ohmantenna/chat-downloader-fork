# SPDX-License-Identifier: MIT

"""Bound decoded Kick HTTP bodies before full buffering and JSON parsing.

Requests/cloudscraper use a response hook with streamed, decoded chunks; the
hook runs before cloudscraper can inspect the body. curl-cffi's stream producer
uses an unbounded queue, so its synchronous native content callback is used
instead. Callback overflow is latched, not raised through the native callback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import requests

from .errors import KickResourceLimit
from .resource_limits import MAX_HISTORY_RESPONSE_BYTES, MAX_OTHER_RESPONSE_BYTES

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Literal

    from curl_cffi.requests import Response as CurlResponse

    from .http_session import _KickSession

_HTTP_CHUNK_BYTES = 16 * 1024


class _ResponseBody:
    """Retain at most the finite decompressed response ceiling."""

    def __init__(self, limit: int, *, history: bool) -> None:
        """Initialize one bounded collection, independent of backend buffering."""
        self.limit = limit
        self.history = history
        self.content = bytearray()
        self.exceeded = False

    def collect(self, chunk: bytes) -> int:
        """Return zero on overflow so libcurl aborts without callback exceptions."""
        if self.exceeded:
            return 0
        remaining = self.limit - len(self.content)
        if len(chunk) > remaining:
            self.content.extend(chunk[:remaining])
            self.exceeded = True
            return 0
        self.content.extend(chunk)
        return len(chunk)


def _consume_response(response: requests.Response, body: _ResponseBody) -> None:
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
    original: requests.Response, body: _ResponseBody
) -> requests.Response:
    """Expose only the bounded bytes to the existing status/challenge policy."""
    response = requests.Response()
    response.status_code = original.status_code
    response.headers.update(original.headers)
    response.encoding = "utf-8" if body.history else getattr(original, "encoding", None)
    response._content = bytes(body.content)
    response._content_consumed = True
    return response


def fetch_bounded_response(
    session: _KickSession,
    method: Literal["GET", "POST"],
    url: str,
    options: dict[str, object],
    *,
    history: bool = False,
) -> tuple[requests.Response, bool]:
    """Fetch a finite body with the backend's pre-buffering boundary."""
    limit = MAX_HISTORY_RESPONSE_BYTES if history else MAX_OTHER_RESPONSE_BYTES
    body = _ResponseBody(limit, history=history)
    request = session.get if method == "GET" else session.post
    kwargs = {**options, "stream": True}
    # This recognizes subclasses without importing an optional backend eagerly.
    if any(cls.__module__.startswith("curl_cffi.") for cls in type(session).__mro__):
        response = _fetch_curl_response(request, url, kwargs, body)
        if history:
            response = _buffered_response(response, body)
        else:
            cast("CurlResponse", response).content = bytes(body.content)
    else:
        response = _fetch_streamed_response(
            request, url, kwargs, body, use_hooks=isinstance(session, requests.Session)
        )
    return response, body.exceeded


def response_limit_error(*, history: bool) -> KickResourceLimit:
    """Build a resource error without including response data."""
    message = "Kick HTTP response byte resource limit exceeded."
    if history:
        message = (
            "Kick history response byte resource limit exceeded; capture is incomplete."
        )
    return KickResourceLimit(message)


def _fetch_streamed_response(
    request: Callable[..., requests.Response],
    url: str,
    kwargs: dict[str, object],
    body: _ResponseBody,
    *,
    use_hooks: bool,
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
        if body.history:
            response.encoding = "utf-8"
        return response

    if use_hooks:
        kwargs["hooks"] = {"response": [bound_response]}
    response = request(url, **kwargs)
    if not consumed:
        _consume_response(response, body)
        response = _buffered_response(response, body)
    return response


def _fetch_curl_response(
    request: Callable[..., requests.Response],
    url: str,
    kwargs: dict[str, object],
    body: _ResponseBody,
) -> requests.Response:
    # stream=True would enqueue arbitrary data before iter_content consumes it.
    kwargs.update(stream=False, content_callback=body.collect)
    try:
        response = request(url, **kwargs)
    except Exception as error:
        wrapped_response = getattr(error, "response", None)
        if not body.exceeded:
            raise
        if wrapped_response is None:
            raise response_limit_error(history=body.history) from None
        response = wrapped_response
    return response

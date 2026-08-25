# SPDX-License-Identifier: MIT

"""Owned client for Kick's unauthenticated web JSON endpoints."""

from __future__ import annotations

from http import HTTPStatus
from json import JSONDecodeError
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from chat_downloader.debugging import logger
from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.utils.json_types import (
    JSONAny,
    JSONDict,
    JSONList,
    get_dict,
    get_list,
)

from .constants import (
    CHANNEL_API_TEMPLATE,
    CHANNEL_MESSAGES_API,
    CLOUDFLARE_MARKERS,
    MESSAGES_API_TEMPLATE,
    VIDEO_API_TEMPLATE,
)
from .errors import KickError, KickServerError
from .http_session import _KickSession, create_kick_session

if TYPE_CHECKING:
    import requests

_DEFAULT_TIMEOUT = (10.0, 30.0)
_ResourceKind = Literal["channel", "video", "messages"]


class KickApiClient:
    """Own one HTTP session and apply one response policy to Kick endpoints."""

    def __init__(
        self,
        *,
        proxy: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: tuple[float, float] = _DEFAULT_TIMEOUT,
        trust_env: bool = True,
        session: _KickSession | None = None,
    ) -> None:
        """Create a client that owns either the supplied or a new session."""
        self._session = session or create_kick_session(
            proxy=dict(proxy) if proxy else None,
            extra_headers=dict(extra_headers) if extra_headers else None,
            trust_env=trust_env,
        )
        self._timeout = tuple(timeout)
        self._closed = False

    def fetch_channel(self, username: str) -> JSONDict:
        """Fetch channel metadata by username."""
        return self._request_object(
            CHANNEL_API_TEMPLATE.format(username=username),
            context=username,
            resource="channel",
        )

    def fetch_preloaded_messages(
        self,
        channel_id: str,
        username: str,
    ) -> JSONList:
        """Fetch and validate recent live-chat history."""
        payload = self._request_object(
            MESSAGES_API_TEMPLATE.format(channel_id=channel_id),
            context=username,
            resource="messages",
        )
        messages = get_list(get_dict(payload, "data"), "messages")
        return cast("JSONList", [item for item in messages if isinstance(item, dict)])

    def fetch_video_metadata(self, video_id: str) -> JSONDict:
        """Fetch VOD metadata by UUID."""
        return self._request_object(
            VIDEO_API_TEMPLATE.format(video_id=video_id),
            context=video_id,
            resource="video",
        )

    def fetch_message_page(
        self,
        channel_id: str,
        cursor: str | None = None,
    ) -> JSONDict:
        """Fetch one VOD message-history page."""
        params = {"cursor": cursor} if cursor else None
        return self._request_object(
            CHANNEL_MESSAGES_API.format(channel_id=channel_id),
            context=channel_id,
            resource="messages",
            params=params,
        )

    def _request_object(
        self,
        url: str,
        *,
        context: str,
        resource: _ResourceKind,
        params: dict[str, str] | None = None,
    ) -> JSONDict:
        """GET one endpoint and require a JSON-object response."""
        session = self._require_open_session()
        response = session.get(url, params=params, timeout=self._timeout)
        if _body_looks_like_challenge(response):
            _raise_for_challenge(response, context)
        _check_status(response, context=context, resource=resource)
        data = _decode_json(response, context=context, resource=resource)
        if not isinstance(data, dict):
            msg = f"Kick {resource} response for {context!r} was not a JSON object."
            raise KickServerError(msg)
        return data

    def _require_open_session(self) -> _KickSession:
        """Return the owned session or fail deterministically after close."""
        if self._closed:
            msg = "KickApiClient is closed."
            raise RuntimeError(msg)
        return self._session

    def close(self) -> None:
        """Close the owned HTTP session exactly once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._session.close()
        except (OSError, RuntimeError) as error:
            logger.debug("Error closing Kick API session: %s", error)


def _body_looks_like_challenge(response: requests.Response) -> bool:
    """Return whether a JSON endpoint returned a challenge document."""
    if any(marker in response.text for marker in CLOUDFLARE_MARKERS):
        return True
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return False
    body_start = response.text.lstrip()[:64].lower()
    return body_start.startswith(("<!doctype html", "<html"))


def _raise_for_challenge(response: requests.Response, context: str) -> NoReturn:
    """Raise the actionable challenge error without logging credentials."""
    logger.debug(
        "Kick request for %r returned a likely challenge (status=%s, content-type=%s).",
        context,
        response.status_code,
        response.headers.get("Content-Type", ""),
    )
    msg = (
        "Kick blocked automated access with a Cloudflare challenge. The"
        " bundled curl-cffi and cloudscraper fallbacks could not bypass it."
        " Changing IP or proxy endpoints can help diagnose reputation issues."
    )
    raise CaptchaChallengeRequired(msg)


def _check_status(
    response: requests.Response,
    *,
    context: str,
    resource: _ResourceKind,
) -> None:
    """Classify endpoint status codes consistently."""
    status = response.status_code
    if status == HTTPStatus.NOT_FOUND:
        if resource == "channel":
            msg = f'Unable to find Kick channel: "{context}"'
            raise UserNotFound(msg)
        msg = f"Kick {resource} not found: {context}"
        raise KickError(msg)
    if status == HTTPStatus.FORBIDDEN:
        _raise_for_challenge(response, context)
    if (
        status == HTTPStatus.TOO_MANY_REQUESTS
        or status >= HTTPStatus.INTERNAL_SERVER_ERROR
    ):
        msg = f"Kick {resource} API returned HTTP {status} for {context!r}."
        raise KickServerError(msg)
    if status != HTTPStatus.OK:
        msg = f"Kick {resource} API returned unexpected HTTP {status} for {context!r}."
        raise KickError(msg)


def _decode_json(
    response: requests.Response,
    *,
    context: str,
    resource: _ResourceKind,
) -> JSONAny:
    """Decode one successful JSON response or classify malformed content."""
    try:
        return cast("JSONAny", response.json())
    except (JSONDecodeError, ValueError) as error:
        msg = f"Kick {resource} API returned malformed JSON for {context!r}."
        raise KickServerError(msg) from error

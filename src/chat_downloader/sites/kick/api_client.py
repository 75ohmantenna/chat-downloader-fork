# SPDX-License-Identifier: MIT

"""Owned client for Kick's unauthenticated JSON endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from json import JSONDecodeError
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from chat_downloader.debugging import logger
from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.redaction import is_sensitive_header
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
    CLIP_API_TEMPLATE,
    CLOUDFLARE_MARKERS,
    MESSAGES_API_TEMPLATE,
    MOBILE_CLIP_API_TEMPLATE,
    VIDEO_API_TEMPLATE,
)
from .errors import KickError, KickForwardHistoryRejected, KickServerError
from .http_session import _KickSession, create_kick_session

if TYPE_CHECKING:
    import requests

_DEFAULT_TIMEOUT = (10.0, 30.0)
_ResourceKind = Literal["channel", "video", "clip", "messages"]


@dataclass(frozen=True, slots=True)
class PreloadedChatState:
    """Recent messages and the current pin returned by Kick's history API."""

    messages: JSONList
    pinned_message: JSONDict | None


class KickApiClient:
    """Own origin-scoped sessions and one response policy for Kick endpoints."""

    def __init__(
        self,
        *,
        proxy: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: tuple[float, float] = _DEFAULT_TIMEOUT,
        trust_env: bool = True,
        session: _KickSession | None = None,
        mobile_session: _KickSession | None = None,
    ) -> None:
        """Create a client with origin-scoped supplied or new sessions."""
        self._session = session or create_kick_session(
            proxy=dict(proxy) if proxy else None,
            extra_headers=dict(extra_headers) if extra_headers else None,
            trust_env=trust_env,
        )
        self._mobile_session = mobile_session
        self._mobile_proxy = dict(proxy) if proxy else None
        self._mobile_extra_headers = {
            name: value
            for name, value in (extra_headers or {}).items()
            if not is_sensitive_header(name, value)
        } or None
        self._mobile_trust_env = trust_env
        self._mobile_header_overrides = {
            name: None
            for name, value in (extra_headers or {}).items()
            if is_sensitive_header(name, value)
        }
        self._timeout = tuple(timeout)
        self._closed = False

    def fetch_channel(self, username: str) -> JSONDict:
        """Fetch channel metadata by username."""
        return self._request_object(
            CHANNEL_API_TEMPLATE.format(username=username),
            context=username,
            resource="channel",
        )

    def fetch_preloaded_chat_state(
        self,
        channel_id: str,
        username: str,
    ) -> PreloadedChatState:
        """Fetch and validate recent live-chat history and current pin state."""
        payload = self._request_object(
            MESSAGES_API_TEMPLATE.format(channel_id=channel_id),
            context=username,
            resource="messages",
        )
        data = get_dict(payload, "data")
        messages = get_list(data, "messages")
        pinned_message = get_dict(data, "pinned_message") or None
        return PreloadedChatState(
            messages=cast(
                "JSONList", [item for item in messages if isinstance(item, dict)]
            ),
            pinned_message=pinned_message,
        )

    def fetch_video_metadata(self, video_id: str) -> JSONDict:
        """Fetch VOD metadata by UUID."""
        return self._request_object(
            VIDEO_API_TEMPLATE.format(video_id=video_id),
            context=video_id,
            resource="video",
        )

    def fetch_clip_metadata(self, clip_id: str) -> JSONDict:
        """Fetch clip metadata by provider clip ID."""
        return self._request_object(
            CLIP_API_TEMPLATE.format(clip_id=clip_id),
            context=clip_id,
            resource="clip",
        )

    def fetch_mobile_clip_metadata(self, clip_id: str) -> JSONDict:
        """Fetch the anonymous mobile clip-metadata fallback."""
        return self._request_object(
            MOBILE_CLIP_API_TEMPLATE.format(clip_id=clip_id),
            context=clip_id,
            resource="clip",
            mobile=True,
        )

    def fetch_message_page(
        self,
        channel_id: str,
        *,
        cursor: str | None = None,
        start_time: str | None = None,
    ) -> JSONDict:
        """Fetch one message-history page in one pagination direction."""
        if cursor and start_time:
            msg = "Kick message history accepts either cursor or start_time."
            raise ValueError(msg)
        params = None
        if cursor:
            params = {"cursor": cursor}
        elif start_time:
            params = {"start_time": start_time}
        return self._request_object(
            CHANNEL_MESSAGES_API.format(channel_id=channel_id),
            context=channel_id,
            resource="messages",
            params=params,
            forward_history=bool(start_time),
        )

    def _request_object(
        self,
        url: str,
        *,
        context: str,
        resource: _ResourceKind,
        params: dict[str, str] | None = None,
        forward_history: bool = False,
        mobile: bool = False,
    ) -> JSONDict:
        """GET one endpoint and require a JSON-object response."""
        session = self._require_open_session(mobile=mobile)
        request_kwargs: dict[str, object] = {
            "params": params,
            "timeout": self._timeout,
        }
        if mobile and self._mobile_header_overrides:
            request_kwargs["headers"] = dict(self._mobile_header_overrides)
        response = session.get(url, **request_kwargs)
        if _body_looks_like_challenge(response):
            _raise_for_challenge(response, context)
        if forward_history and _response_rejects_start_time(response):
            msg = f"Kick rejected forward message history for {context!r}."
            raise KickForwardHistoryRejected(msg)
        _check_status(response, context=context, resource=resource)
        data = _decode_json(response, context=context, resource=resource)
        if not isinstance(data, dict):
            msg = f"Kick {resource} response for {context!r} was not a JSON object."
            raise KickServerError(msg)
        return data

    def _require_open_session(self, *, mobile: bool = False) -> _KickSession:
        """Return the owned session or fail deterministically after close."""
        if self._closed:
            msg = "KickApiClient is closed."
            raise RuntimeError(msg)
        if mobile:
            mobile_session = self._mobile_session
            if mobile_session is None:
                mobile_session = create_kick_session(
                    proxy=self._mobile_proxy,
                    extra_headers=self._mobile_extra_headers,
                    trust_env=self._mobile_trust_env,
                )
                self._mobile_session = mobile_session
            return mobile_session
        return self._session

    def close(self) -> None:
        """Close the owned HTTP sessions exactly once."""
        if self._closed:
            return
        self._closed = True
        sessions = [self._session]
        if (
            self._mobile_session is not None
            and self._mobile_session is not self._session
        ):
            sessions.append(self._mobile_session)
        for session in sessions:
            try:
                session.close()
            except (OSError, RuntimeError) as error:
                logger.debug("Error closing Kick API session: %s", error)


def _response_rejects_start_time(response: requests.Response) -> bool:
    """Return whether a 400/422 response names ``start_time`` as invalid."""
    if response.status_code not in {
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    }:
        return False
    try:
        payload = response.json()
    except (JSONDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return "start_time" in get_dict(payload, "errors")


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

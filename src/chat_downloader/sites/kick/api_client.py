# SPDX-License-Identifier: MIT

"""HTTP helpers for Kick's public, unauthenticated v2 JSON API.

These functions use :func:`_get_kick_session` — a dedicated session with
``cloudscraper`` Cloudflare bypass when available — and centralize response
handling: Cloudflare/challenge detection, not-found handling, and
transient-error classification. They perform *no* parsing of chat content;
that lives in :mod:`chat_downloader.sites.kick.parsing`.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, NoReturn

import requests

from chat_downloader.debugging import logger
from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound

from .constants import (
    CHANNEL_API_TEMPLATE,
    CLOUDFLARE_MARKERS,
    MESSAGES_API_TEMPLATE,
)
from .errors import KickError, KickServerError

_KICK_SESSION: requests.Session | None = None


def _get_kick_session(
    *,
    proxy: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> requests.Session:  # pragma: no cover — replaced by FakeKickSession in tests
    """Create an HTTP session with Cloudflare bypass for Kick.com API.

    Uses ``cloudscraper`` when available for automatic Cloudflare challenge
    handling. Falls back to a plain ``requests.Session`` with Kick-specific
    headers (User-Agent, Accept, Referer).

    Args:
        proxy: Optional proxy mapping (e.g. ``{"http": "...", "https": "..."}``)
            applied to the session. Only takes effect on fresh sessions; the
            singleton returned on subsequent calls retains its original config.
        extra_headers: Optional headers dict merged into the session's default
            headers. Only takes effect on fresh sessions; the cached singleton
            retains its original config.

    Returns:
        A configured ``requests.Session`` (or ``cloudscraper`` session).
    """
    global _KICK_SESSION  # noqa: PLW0603 — lazy-init singleton
    if _KICK_SESSION is None:
        try:
            import cloudscraper  # type: ignore[import-untyped]

            _KICK_SESSION = cloudscraper.create_scraper()
        except ImportError:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://kick.com/",
                    "DNT": "1",
                }
            )
            _KICK_SESSION = session
    if proxy:
        _KICK_SESSION.proxies.update(proxy)
    if extra_headers:
        _KICK_SESSION.headers.update(extra_headers)
    return _KICK_SESSION


def _body_looks_like_challenge(response: requests.Response) -> bool:
    """Return ``True`` if a response *body* looks like a challenge page.

    Status-code-based detection (e.g. HTTP 403) is handled separately in
    :func:`_check_status`; this inspects only the body, so a JSON API that
    returns an HTML document (with or without explicit Cloudflare markers) is
    treated as a likely challenge.

    Args:
        response: The HTTP response to inspect.

    Returns:
        ``True`` when the body contains known Cloudflare markers or is HTML.
    """
    if any(marker in response.text for marker in CLOUDFLARE_MARKERS):
        return True
    content_type = response.headers.get("Content-Type", "").lower()
    if not isinstance(content_type, str) or "text/html" not in content_type:
        return False
    body_start = response.text.lstrip()[:64].lower()
    return body_start.startswith(("<!doctype html", "<html"))


def _raise_for_challenge(response: requests.Response, username: str) -> NoReturn:
    """Raise :class:`CaptchaChallengeRequired` for a challenge response.

    Args:
        response: The HTTP response that looked like a challenge.
        username: Channel username, used only for a sanitized log line.

    Raises:
        CaptchaChallengeRequired: Always, with an actionable message.
    """
    logger.debug(
        "Kick request for channel %r returned a likely challenge "
        "(status=%s, content-type=%s).",
        username,
        response.status_code,
        response.headers.get("Content-Type", ""),
    )
    msg = (
        "Kick blocked unauthenticated automated access (likely a Cloudflare "
        "challenge). This implementation does not bypass challenges. Your "
        "VPN/proxy endpoint reputation may contribute; changing endpoint can "
        "help diagnose network reputation or rate-limit issues."
    )
    raise CaptchaChallengeRequired(msg)


def _decode_json(response: requests.Response, username: str) -> Any:
    """Decode a JSON response, mapping a challenge HTML body to a clear error.

    Args:
        response: The HTTP response to decode.
        username: Channel username, used for error context.

    Returns:
        The decoded JSON payload.

    Raises:
        CaptchaChallengeRequired: If the body is an HTML challenge page.
        KickServerError: If the body is malformed JSON (transient/retryable).
    """
    from json import JSONDecodeError

    try:
        return response.json()
    except (JSONDecodeError, ValueError) as error:
        if _body_looks_like_challenge(response):
            _raise_for_challenge(response, username)
        msg = f"Kick returned a malformed JSON response for channel {username!r}."
        raise KickServerError(msg) from error


def _check_status(response: requests.Response, username: str) -> None:
    """Raise an appropriate error for a non-OK status code.

    Args:
        response: The HTTP response to validate.
        username: Channel username, used for error context.

    Raises:
        UserNotFound: On HTTP 404.
        CaptchaChallengeRequired: On HTTP 403 / challenge responses.
        KickServerError: On HTTP 429 or 5xx (transient/retryable).
    """
    status = response.status_code
    if status == HTTPStatus.NOT_FOUND:
        msg = f'Unable to find Kick channel: "{username}"'
        raise UserNotFound(msg)
    if status == HTTPStatus.FORBIDDEN:
        _raise_for_challenge(response, username)
    if (
        status == HTTPStatus.TOO_MANY_REQUESTS
        or status >= HTTPStatus.INTERNAL_SERVER_ERROR
    ):
        msg = f"Kick API returned HTTP {status} for channel {username!r}."
        raise KickServerError(msg)
    if status != HTTPStatus.OK:
        msg = f"Kick API returned unexpected HTTP {status} for channel {username!r}."
        raise KickError(msg)


def fetch_channel(
    username: str,
    *,
    proxy: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch channel metadata from ``/api/v2/channels/{username}``.

    Uses a dedicated HTTP session (with ``cloudscraper`` Cloudflare bypass
    when available) to make the request.

    Args:
        username: Channel username/slug.
        proxy: Optional proxy mapping for the HTTP session.
        extra_headers: Optional headers to merge into the session.

    Returns:
        The decoded channel metadata object.

    Raises:
        UserNotFound: If the channel does not exist.
        CaptchaChallengeRequired: If Kick returns a challenge page.
        KickServerError: On transient (429/5xx/malformed) responses.
    """
    url = CHANNEL_API_TEMPLATE.format(username=username)
    response = _get_kick_session(proxy=proxy, extra_headers=extra_headers).get(
        url, timeout=(10, 30)
    )
    _check_status(response, username)
    data = _decode_json(response, username)
    if not isinstance(data, dict):
        msg = f"Kick channel metadata for {username!r} was not a JSON object."
        raise KickServerError(msg)
    return data


def fetch_preloaded_messages(
    channel_id: str,
    username: str,
    *,
    proxy: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent preloaded messages from ``/channels/{id}/messages``.

    Uses a dedicated HTTP session (with ``cloudscraper`` Cloudflare bypass
    when available) to make the request.

    Args:
        channel_id: Numeric channel id.
        username: Channel username/slug, used only for error context.
        proxy: Optional proxy mapping for the HTTP session.
        extra_headers: Optional headers to merge into the session.

    Returns:
        A list of raw preloaded message objects (possibly empty). Failures to
        retrieve preloaded history are non-fatal and yield an empty list, since
        the live websocket stream is the primary source.
    """
    url = MESSAGES_API_TEMPLATE.format(channel_id=channel_id)
    try:
        response = _get_kick_session(proxy=proxy, extra_headers=extra_headers).get(
            url, timeout=(10, 30)
        )
        _check_status(response, username)
        data = _decode_json(response, username)
    except KickServerError as error:
        logger.debug("Kick preloaded-message fetch failed (non-fatal): %s", error)
        return []

    messages = data.get("data", {}).get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]

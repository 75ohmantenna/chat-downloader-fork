# SPDX-License-Identifier: MIT

"""Kick VOD (video-on-demand) chat replay.

Fetches chat messages for a past broadcast by paginating through the
channel's message history and filtering by the VOD's time window.

The VOD chat is served through the same ``api/v2/channels/{id}/messages``
endpoint as preloaded live messages, but filtered to the VOD's start-to-end
time range. Messages arrive newest-first; the generator reverses them to
chronological order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError, RetriesExceeded
from chat_downloader.sites.models import Chat
from chat_downloader.sites.retry import retry
from chat_downloader.utils.json_types import (
    JSONAny,
    JSONDict,
    JSONList,
    get_dict,
    get_list,
)

from .api_client import _close_kick_session, _get_kick_session
from .constants import CHANNEL_MESSAGES_API, VIDEO_API_TEMPLATE, is_numeric_id
from .errors import KickError, KickServerError
from .parsing.messages import parse_chat_message

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.models import ChatRequest

    from .api_client import _KickSession


def _fetch_with_retry[T](fetch: Callable[[], T], request: ChatRequest) -> T:
    """Run a transient Kick VOD request with the configured retry policy."""
    for attempt_number in range(1, request.max_attempts + 1):
        try:
            return fetch()
        except (
            KickServerError,
            RequestException,
            JSONDecodeError,
            OSError,
        ) as error:
            retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover


def _fetch_video_metadata(
    video_id: str,
    *,
    proxy: dict[str, str] | None = None,
    session: _KickSession | None = None,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> JSONDict:  # pragma: no cover — network-dependent VOD API
    """Fetch video metadata from ``/api/v1/video/{video_id}``.

    Args:
        video_id: The VOD UUID.
        proxy: Optional proxy mapping for the HTTP session.
        session: Optional caller-owned Kick API session.
        timeout: Connect/read timeout tuple for the API request.

    Returns:
        The decoded video metadata object.

    Raises:
        KickError: If the video is not found or metadata is incomplete.
    """
    owns_session = session is None
    active_session = session or _get_kick_session(proxy=proxy)
    url = VIDEO_API_TEMPLATE.format(video_id=video_id)
    try:
        resp = active_session.get(url, timeout=timeout)
        if resp.status_code == 404:
            msg = f"Kick video not found: {video_id}"
            raise KickError(msg)
        if resp.status_code == 429 or resp.status_code >= 500:
            msg = f"Kick video API returned HTTP {resp.status_code} for {video_id}"
            raise KickServerError(msg)
        if not resp.ok:
            msg = f"Kick video API returned HTTP {resp.status_code} for {video_id}"
            raise KickError(msg)
        data = cast("JSONAny", resp.json())
        if not isinstance(data, dict):
            msg = f"Kick video metadata for {video_id} was not a JSON object."
            raise KickServerError(msg)
        return data
    finally:
        if owns_session:
            _close_kick_session(active_session)


def _resolve_vod_window(  # pragma: no cover — network-dependent; tested elsewhere
    data: JSONDict, username: str
) -> tuple[str, str, str, datetime, datetime]:
    """Resolve the channel id, title, and VOD time window from video metadata.

    Args:
        data: Video metadata object.
        username: Channel username/slug.

    Returns:
        A ``(channel_id, chatroom_id, title, start_dt, end_dt)`` tuple.

    Raises:
        KickError: If required fields are missing.
    """
    livestream = data.get("livestream")
    if not isinstance(livestream, dict):
        msg = f"Kick video for {username!r} has no associated livestream data."
        raise KickError(msg)

    channel = livestream.get("channel")
    channel_id = str(channel.get("id")) if isinstance(channel, dict) else None
    if not channel_id:
        msg = f"Kick video for {username!r} is missing a channel id."
        raise KickError(msg)
    if not is_numeric_id(channel_id):
        msg = f"Kick video for {username!r} returned a non-numeric channel id."
        raise KickError(msg)

    chatroom_id = ""
    if isinstance(channel, dict):
        chatroom_data = channel.get("chatroom")
        if isinstance(chatroom_data, dict):
            chatroom_id = str(chatroom_data.get("id", ""))

    title = str(livestream.get("session_title", username))

    # Parse VOD time window
    start_time_raw = livestream.get("start_time")
    if not isinstance(start_time_raw, str):
        msg = f"Kick video for {username!r} is missing a start_time."
        raise KickError(msg)

    try:
        start_dt = datetime.fromisoformat(start_time_raw)
    except (ValueError, TypeError) as error:
        msg = f"Kick video for {username!r} has an unparsable start_time: {error}"
        raise KickError(msg) from error

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    duration_ms = livestream.get("duration", 0)
    duration_seconds = (
        duration_ms if isinstance(duration_ms, (int, float)) else 0
    ) / 1000
    end_dt = start_dt + timedelta(seconds=duration_seconds)

    return channel_id, chatroom_id, title, start_dt, end_dt


def _fetch_message_page(  # pragma: no cover — network-dependent
    channel_id: str,
    cursor: str | None = None,
    *,
    proxy: dict[str, str] | None = None,
    session: _KickSession | None = None,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> JSONDict:
    """Fetch one page of channel messages.

    Args:
        channel_id: Numeric channel id.
        cursor: Optional pagination cursor (timestamp).
        proxy: Optional proxy mapping for the HTTP session.
        session: Optional caller-owned Kick API session.
        timeout: Connect/read timeout tuple for the API request.

    Returns:
        The API response dict with ``data.messages`` and ``data.cursor``.
    """
    owns_session = session is None
    active_session = session or _get_kick_session(proxy=proxy)
    url = CHANNEL_MESSAGES_API.format(channel_id=channel_id)
    params = {"cursor": cursor} if cursor else None
    try:
        resp = active_session.get(url, params=params, timeout=timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            msg = f"Kick messages API returned HTTP {resp.status_code}"
            raise KickServerError(msg)
        if not resp.ok:
            msg = f"Kick messages API returned HTTP {resp.status_code}"
            raise KickError(msg)
        try:
            data = cast("JSONAny", resp.json())
        except (JSONDecodeError, ValueError) as error:
            msg = "Kick messages API returned malformed JSON."
            raise KickServerError(msg) from error
        if not isinstance(data, dict):
            msg = "Kick messages API returned a non-object payload."
            raise KickServerError(msg)
        return data
    finally:
        if owns_session:
            _close_kick_session(active_session)


def get_vod_chat(  # pragma: no cover — network-dependent
    username: str,
    video_id: str,
    request: ChatRequest,
    *,
    proxy: dict[str, str] | None = None,
    session: _KickSession | None = None,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> Chat:
    """Build a :class:`Chat` for VOD chat replay.

    Paginates through the channel's message history, filters by the
    VOD's time window, and returns messages in chronological order.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        video_id: VOD UUID.
        request: The active chat request.
        proxy: Optional proxy mapping for the HTTP session.
        session: Optional caller-owned Kick API session.
        timeout: Connect/read timeout tuple for API requests.

    Returns:
        A configured :class:`Chat` whose generator yields message dicts.
    """
    video_data = _fetch_with_retry(
        lambda: _fetch_video_metadata(
            video_id,
            proxy=proxy,
            session=session,
            timeout=timeout,
        ),
        request,
    )
    channel_id, _chatroom_id, title, start_dt, end_dt = _resolve_vod_window(
        video_data, username
    )

    log("info", f"VOD time window: {start_dt} to {end_dt}")

    return Chat(
        _iter_vod_messages(
            channel_id,
            start_dt,
            end_dt,
            request,
            proxy=proxy,
            session=session,
            timeout=timeout,
        ),
        title=title,
        status="completed",
        video_type="video",
        id=video_id,
    )


def _classify_message(
    raw: dict[str, Any], start_dt: datetime, end_dt: datetime
) -> tuple[dict[str, Any] | None, bool]:
    """Classify a raw message as in-window, out-of-range, or unparsable.

    Args:
        raw: Raw message dict.
        start_dt: VOD start time.
        end_dt: VOD end time.

    Returns:
        ``(parsed, done)`` where ``parsed`` is the normalized message if within
        the window, ``None`` otherwise; ``done`` is ``True`` when we've passed
        the VOD start (messages come newest-first so older messages are past).
    """
    created_raw = raw.get("created_at", "")
    if not isinstance(created_raw, str):
        return None, False
    try:
        msg_dt = datetime.fromisoformat(created_raw)
    except (ValueError, TypeError):
        return None, False

    if msg_dt.tzinfo is None:
        msg_dt = msg_dt.replace(tzinfo=UTC)

    if msg_dt < start_dt:
        return None, True
    if msg_dt > end_dt:
        return None, False

    try:
        parsed = parse_chat_message(raw)
    except ParsingError:
        return None, False
    else:
        return parsed, False


def _iter_vod_messages(  # pragma: no cover — calls _fetch_message_page
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    proxy: dict[str, str] | None = None,
    session: _KickSession | None = None,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> Generator[dict[str, Any], None, None]:
    """Yield normalized VOD chat messages within the time window.

    Paginates through channel messages (newest first) and yields those
    whose ``created_at`` falls within the VOD's time window. Unlike live
    chat which streams messages as they arrive, VOD replay accumulates
    all pages (up to a 500-page ceiling), reverses to chronological
    order, and yields the result. With ``max_messages`` set, the oldest
    *N* messages (i.e. the first *N* from the stream start) are returned.
    """
    cursor: str | None = None
    all_messages: list[dict[str, Any]] = []
    done = False
    pages = 0

    while not done and pages < 500:
        page = _fetch_with_retry(
            partial(
                _fetch_message_page,
                channel_id,
                cursor,
                proxy=proxy,
                session=session,
                timeout=timeout,
            ),
            request,
        )
        data_section = get_dict(page, "data")
        raw_messages: JSONList = get_list(data_section, "messages")
        cursor_val = data_section.get("cursor")
        cursor = cursor_val if isinstance(cursor_val, str) else None

        if not raw_messages:
            break

        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            parsed, msg_done = _classify_message(raw, start_dt, end_dt)
            if msg_done:
                done = True
                break
            if parsed is not None:
                all_messages.append(parsed)

        pages += 1

        if not cursor or done:
            break

    # Messages arrive newest-first; reverse to chronological.
    # max_messages means the oldest N (first N of the VOD).
    all_messages.reverse()
    if request.max_messages:
        all_messages = all_messages[: request.max_messages]

    yield from all_messages

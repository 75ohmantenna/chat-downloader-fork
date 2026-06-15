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

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log, logger
from chat_downloader.errors import ParsingError
from chat_downloader.sites.models import Chat

from .api_client import _get_kick_session
from .constants import CHANNEL_MESSAGES_API, VIDEO_API_TEMPLATE
from .errors import KickError, KickServerError
from .parsing.messages import parse_chat_message

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest


def _fetch_video_metadata(
    video_id: str,
) -> dict[str, Any]:  # pragma: no cover — network-dependent VOD API
    """Fetch video metadata from ``/api/v1/video/{video_id}``.

    Args:
        video_id: The VOD UUID.

    Returns:
        The decoded video metadata object.

    Raises:
        KickError: If the video is not found or metadata is incomplete.
    """
    session = _get_kick_session()
    url = VIDEO_API_TEMPLATE.format(video_id=video_id)
    resp = session.get(url, timeout=(10, 30))
    if resp.status_code == 404:
        msg = f"Kick video not found: {video_id}"
        raise KickError(msg)
    if not resp.ok:
        msg = f"Kick video API returned HTTP {resp.status_code} for {video_id}"
        raise KickServerError(msg)
    data = resp.json()
    if not isinstance(data, dict):
        msg = f"Kick video metadata for {video_id} was not a JSON object."
        raise KickServerError(msg)
    return data


def _resolve_vod_window(  # pragma: no cover — network-dependent; tested elsewhere
    data: dict[str, Any], username: str
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
        msg = f"Kick video for {username!r} has an unparseable start_time: {error}"
        raise KickError(msg) from error

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)  # noqa: UP017 — mypy doesn't recognise datetime.UTC

    duration_ms = livestream.get("duration", 0)
    duration_seconds = (
        duration_ms if isinstance(duration_ms, (int, float)) else 0
    ) / 1000
    end_dt = start_dt + timedelta(seconds=duration_seconds)

    return channel_id, chatroom_id, title, start_dt, end_dt


def _fetch_message_page(  # pragma: no cover — network-dependent
    channel_id: str, cursor: str | None = None
) -> dict[str, Any]:
    """Fetch one page of channel messages.

    Args:
        channel_id: Numeric channel id.
        cursor: Optional pagination cursor (timestamp).

    Returns:
        The API response dict with ``data.messages`` and ``data.cursor``.
    """
    session = _get_kick_session()
    url = CHANNEL_MESSAGES_API.format(channel_id=channel_id)
    if cursor:
        url = f"{url}?cursor={cursor}"
    resp = session.get(url, timeout=(10, 30))
    if not resp.ok:
        logger.debug("Kick messages API returned HTTP %s", resp.status_code)
        return {"data": {"messages": [], "cursor": None}}
    data = resp.json()
    return (
        data if isinstance(data, dict) else {"data": {"messages": [], "cursor": None}}
    )


def get_vod_chat(  # pragma: no cover — network-dependent
    username: str,
    video_id: str,
    request: ChatRequest,
) -> Chat:
    """Build a :class:`Chat` for VOD chat replay.

    Paginates through the channel's message history, filters by the
    VOD's time window, and returns messages in chronological order.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        video_id: VOD UUID.
        request: The active chat request.

    Returns:
        A configured :class:`Chat` whose generator yields message dicts.
    """
    video_data = _fetch_video_metadata(video_id)
    channel_id, _chatroom_id, title, start_dt, end_dt = _resolve_vod_window(
        video_data, username
    )

    log("info", f"VOD time window: {start_dt} to {end_dt}")

    return Chat(
        _iter_vod_messages(channel_id, start_dt, end_dt, request),
        title=title,
        status="completed",
        video_type="video",
        id=video_id,
    )


def _classify_message(
    raw: dict[str, Any], start_dt: datetime, end_dt: datetime
) -> tuple[dict[str, Any] | None, bool]:
    """Classify a raw message as in-window, out-of-range, or unparseable.

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
) -> Generator[dict[str, Any], None, None]:
    """Yield normalized VOD chat messages within the time window.

    Paginates through channel messages (newest first) and yields those
    whose ``created_at`` falls within the VOD's time window.

    Args:
        channel_id: Numeric channel id.
        start_dt: VOD start time.
        end_dt: VOD end time.
        request: The active chat request (for ``max_messages`` limit).

    Yields:
        Normalized message dicts in chronological order.
    """
    cursor: str | None = None
    all_messages: list[dict[str, Any]] = []
    done = False
    pages = 0

    while not done and pages < 500:
        page = _fetch_message_page(channel_id, cursor)
        data = page.get("data", {})
        raw_messages = data.get("messages", [])
        cursor = data.get("cursor")

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

        if request.max_messages and len(all_messages) >= request.max_messages:
            break

    # Messages arrive newest-first; reverse to chronological
    all_messages.reverse()

    if request.max_messages:
        all_messages = all_messages[: request.max_messages]

    yield from all_messages

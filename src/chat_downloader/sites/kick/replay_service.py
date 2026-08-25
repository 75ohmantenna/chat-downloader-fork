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

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError, RetriesExceeded
from chat_downloader.sites.models import Chat
from chat_downloader.sites.retry import retry
from chat_downloader.utils.json_types import JSONDict, JSONList, get_dict, get_list
from chat_downloader.utils.time_utils import ensure_seconds

from .constants import is_numeric_id
from .errors import KickError, KickServerError
from .parsing.messages import parse_chat_message

_VOD_SPOOL_MEMORY_BYTES = 1024 * 1024

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient


def _fetch_with_retry[T](fetch: Callable[[], T], request: ChatRequest) -> T:
    """Run a transient Kick VOD request with the configured retry policy."""
    for attempt_number in range(1, request.max_attempts + 1):
        try:
            return fetch()
        except (
            KickServerError,
            RequestException,
            OSError,
        ) as error:
            retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover


def _resolve_vod_window(
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


def get_vod_chat(
    username: str,
    video_id: str,
    request: ChatRequest,
    *,
    api_client: KickApiClient,
) -> Chat:
    """Build a :class:`Chat` for VOD chat replay.

    Paginates through the channel's message history, filters by the
    VOD's time window, and returns messages in chronological order.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        video_id: VOD UUID.
        request: The active chat request.
        api_client: Downloader-owned provider HTTP client.

    Returns:
        A configured :class:`Chat` whose generator yields message dicts.
    """
    video_data = _fetch_with_retry(
        lambda: api_client.fetch_video_metadata(video_id),
        request,
    )
    channel_id, _chatroom_id, title, vod_start_dt, vod_end_dt = _resolve_vod_window(
        video_data, username
    )
    start_dt, end_dt = _apply_request_window(vod_start_dt, vod_end_dt, request)

    log("info", f"VOD time window: {start_dt} to {end_dt}")

    return Chat(
        _iter_vod_messages(
            channel_id,
            start_dt,
            end_dt,
            request,
            api_client=api_client,
        ),
        title=title,
        status="completed",
        video_type="video",
        start_time=(start_dt - vod_start_dt).total_seconds(),
        duration=max(0.0, (end_dt - start_dt).total_seconds()),
        id=video_id,
    )


def _apply_request_window(
    vod_start_dt: datetime,
    vod_end_dt: datetime,
    request: ChatRequest,
) -> tuple[datetime, datetime]:
    """Apply request-relative offsets to a VOD's absolute time window."""
    duration = max(0.0, (vod_end_dt - vod_start_dt).total_seconds())
    start_offset = cast("float", ensure_seconds(request.start_time, 0.0))
    end_offset = cast("float", ensure_seconds(request.end_time, duration))

    bounded_start = min(max(start_offset, 0.0), duration)
    bounded_end = min(max(end_offset, 0.0), duration)
    return (
        vod_start_dt + timedelta(seconds=bounded_start),
        vod_start_dt + timedelta(seconds=bounded_end),
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


def _cursor_after(timestamp: datetime) -> str:
    """Return a provider cursor safely after an inclusive end timestamp.

    Kick's history cursor is a UTC Unix timestamp in microseconds and the
    endpoint returns messages strictly before it, while message timestamps may
    be second-granular. Advancing one second avoids dropping messages whose
    displayed timestamp equals the inclusive replay end; classification still
    filters any precisely timestamped message beyond the boundary.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = timestamp - epoch
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
        + 1_000_000
    )
    return str(microseconds)


def _iter_vod_messages(  # noqa: C901 — reverse pagination and spooled output require branch handling
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    api_client: KickApiClient,
) -> Generator[dict[str, Any], None, None]:
    """Yield normalized VOD chat messages within the time window.

    Seeds pagination at the selected end time, then pages through channel
    messages (newest first) and yields those whose ``created_at`` falls within
    the VOD's time window. Unlike live chat which streams messages as they
    arrive, VOD replay accumulates all pages, reverses them to chronological
    order, and yields the result. Cursor cycles terminate pagination safely.
    With ``max_messages`` set, the oldest *N* messages (i.e. the first *N* from
    the stream start) are returned.
    """
    cursor: str | None = _cursor_after(end_dt)
    done = False
    page_offsets: list[int] = []
    requested_cursors: set[str] = set()
    seen_page_digests: set[bytes] = set()

    # The API is newest-first, so chronological output requires a reverse
    # pass. Spill page batches to disk after 1 MiB instead of retaining an
    # entire multi-hour replay in RAM.
    with tempfile.SpooledTemporaryFile(max_size=_VOD_SPOOL_MEMORY_BYTES) as spool:
        while not done:
            if cursor is not None:
                if cursor in requested_cursors:
                    log(
                        "warning",
                        "Kick VOD pagination cursor repeated; stopping to avoid "
                        "duplicate pages.",
                    )
                    break
                requested_cursors.add(cursor)
            page = _fetch_with_retry(
                partial(
                    api_client.fetch_message_page,
                    channel_id,
                    cursor,
                ),
                request,
            )
            data_section = get_dict(page, "data")
            raw_messages: JSONList = get_list(data_section, "messages")
            cursor_val = data_section.get("cursor")
            cursor = cursor_val if isinstance(cursor_val, str) else None

            if not raw_messages:
                break
            page_digest = hashlib.sha256(
                json.dumps(raw_messages, sort_keys=True).encode("utf-8")
            ).digest()
            if page_digest in seen_page_digests:
                log(
                    "warning",
                    "Kick VOD pagination returned a duplicate page; stopping.",
                )
                break
            seen_page_digests.add(page_digest)

            page_messages: list[JSONDict] = []
            for raw in raw_messages:
                if not isinstance(raw, dict):
                    continue
                parsed, msg_done = _classify_message(raw, start_dt, end_dt)
                if msg_done:
                    done = True
                    break
                if parsed is not None:
                    page_messages.append(parsed)

            if page_messages:
                page_offsets.append(spool.tell())
                spool.write(json.dumps(page_messages).encode("utf-8") + b"\n")
            if not cursor or done:
                break

        emitted = 0
        for page_offset in reversed(page_offsets):
            spool.seek(page_offset)
            page_messages = cast("list[JSONDict]", json.loads(spool.readline()))
            for message in reversed(page_messages):
                if request.max_messages is not None and emitted >= request.max_messages:
                    return
                emitted += 1
                yield message

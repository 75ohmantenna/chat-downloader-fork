# SPDX-License-Identifier: MIT

"""Chronological pagination for Kick's timestamp-addressable chat history."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from chat_downloader.debugging import log
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.utils.json_types import JSONDict, JSONList, get_dict, get_list

from .errors import KickError, KickForwardHistoryRejected, KickServerError
from .request_retry import fetch_with_retry

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient

_MICROSECONDS_PER_SECOND = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_HISTORY_SEEN_MESSAGE_LIMIT = 10_000


def _as_utc(timestamp: datetime) -> datetime:
    """Return an aware UTC timestamp, treating naive provider values as UTC."""
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def format_history_start(timestamp: datetime) -> str:
    """Format a timestamp for Kick's inclusive ``start_time`` parameter."""
    return _as_utc(timestamp).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _start_after_cursor(cursor: str) -> str | None:
    """Convert a microsecond Unix cursor to the next exact UTC instant."""
    if not cursor.isascii() or not cursor.isdigit():
        return None
    try:
        microseconds = int(cursor) + 1
    except ValueError:
        return None
    seconds, remainder = divmod(microseconds, _MICROSECONDS_PER_SECOND)
    try:
        timestamp = _EPOCH + timedelta(seconds=seconds, microseconds=remainder)
    except OverflowError:
        return None
    return format_history_start(timestamp)


def _message_timestamp(raw: JSONDict) -> datetime | None:
    """Return one message's normalized provider timestamp when parseable."""
    created_at = raw.get("created_at")
    if not isinstance(created_at, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(created_at))
    except ValueError:
        return None


def _message_identity(raw: JSONDict) -> str | None:
    """Return the parser-equivalent string identity for one raw message."""
    message_id = raw.get("id")
    return None if message_id is None else str(message_id)


def _ordered_page_messages(
    raw_messages: JSONList,
) -> list[tuple[datetime, int, JSONDict]]:
    """Return timestamped page messages in stable chronological order."""
    timestamped: list[tuple[datetime, int, JSONDict]] = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            continue
        timestamp = _message_timestamp(raw)
        if timestamp is not None:
            timestamped.append((timestamp, index, raw))
    timestamped.sort(key=lambda item: (item[0], item[1]))
    return timestamped


def fetch_validated_page(
    api_client: KickApiClient,
    channel_id: str,
    *,
    cursor: str | None = None,
    start_time: str | None = None,
) -> tuple[JSONList, str | None]:
    """Fetch one page and require its nested message-list contract."""
    if start_time is not None:
        page = api_client.fetch_message_page(channel_id, start_time=start_time)
    else:
        page = api_client.fetch_message_page(channel_id, cursor=cursor)
    data_section = get_dict(page, "data")
    if not isinstance(data_section.get("messages"), list):
        msg = f"Kick message history for {channel_id!r} had no message list."
        raise KickServerError(msg)
    raw_messages: JSONList = get_list(data_section, "messages")
    cursor_value = data_section.get("cursor")
    if cursor_value is not None and not isinstance(cursor_value, str):
        msg = f"Kick message history for {channel_id!r} had an invalid cursor."
        raise KickServerError(msg)
    return raw_messages, cursor_value


def iter_forward_history(  # noqa: C901 — pagination guards are one protocol behavior
    api_client: KickApiClient,
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
) -> Generator[JSONDict, None, None]:
    """Yield raw Kick history records chronologically within an inclusive window.

    The provider returns a Unix-microsecond cursor for the last instant in a
    page. Advancing it with integer arithmetic avoids floating-point timestamp
    loss. Provider message timestamps can have lower precision than that
    cursor, so overlap suppression uses message IDs instead of comparing
    visible timestamps to the page cursor.
    """
    start_dt = _as_utc(start_dt)
    end_dt = _as_utc(end_dt)
    if end_dt <= start_dt:
        return

    start_time = format_history_start(start_dt)
    current_start_dt = start_dt
    seen_page_digests: set[bytes] = set()
    seen_messages = _SeenMessageCache(limit=_HISTORY_SEEN_MESSAGE_LIMIT)
    last_yielded_timestamp: datetime | None = None
    first_page = True

    while True:
        try:
            raw_messages, cursor = fetch_with_retry(
                partial(
                    fetch_validated_page,
                    api_client,
                    channel_id,
                    start_time=start_time,
                ),
                request,
            )
        except KickForwardHistoryRejected as error:
            if first_page:
                raise
            msg = "Kick rejected forward history after pagination began."
            raise KickError(msg) from error
        first_page = False
        if not raw_messages:
            return

        page_digest = hashlib.sha256(
            json.dumps(raw_messages, sort_keys=True).encode("utf-8")
        ).digest()
        if page_digest in seen_page_digests:
            log(
                "warning",
                "Kick forward history returned a duplicate page; stopping.",
            )
            return
        seen_page_digests.add(page_digest)

        reached_end = False
        for timestamp, _index, raw in _ordered_page_messages(raw_messages):
            if timestamp < start_dt:
                continue
            if timestamp > end_dt:
                reached_end = True
                break
            if (
                last_yielded_timestamp is not None
                and timestamp < last_yielded_timestamp
            ):
                continue
            message_id = _message_identity(raw)
            if message_id is not None:
                is_new, _evicted = seen_messages.register(message_id)
                if not is_new:
                    continue
            yield raw
            last_yielded_timestamp = timestamp
        if reached_end:
            return

        next_start = _start_after_cursor(cursor) if cursor is not None else None
        if next_start is None:
            if cursor is not None:
                log(
                    "warning",
                    "Kick forward history returned an invalid cursor; stopping.",
                )
            return
        next_start_dt = datetime.fromisoformat(next_start)
        if next_start_dt <= current_start_dt:
            log(
                "warning",
                "Kick forward history cursor did not advance; stopping.",
            )
            return
        start_time = next_start
        current_start_dt = next_start_dt

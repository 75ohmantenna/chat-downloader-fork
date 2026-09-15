# SPDX-License-Identifier: MIT

"""Chronological pagination for Kick's timestamp-addressable chat history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from chat_downloader.debugging import log
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.utils.json_types import JSONDict, JSONList, get_dict, get_list

from .errors import KickServerError
from .request_retry import fetch_with_retry

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient

_HISTORY_SEEN_MESSAGE_LIMIT = 10_000


def _as_utc(timestamp: datetime) -> datetime:
    """Return an aware UTC timestamp, treating naive provider values as UTC."""
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def format_history_start(timestamp: datetime) -> str:
    """Format a timestamp for Kick's inclusive ``start_time`` parameter."""
    return _as_utc(timestamp).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def iter_forward_history(  # noqa: C901 — bounded history window traversal
    api_client: KickApiClient,
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    max_pages: int | None = None,
    max_records: int | None = None,
) -> Generator[JSONDict, None, None]:
    """Read five-second history windows, as the Kick website does.

    The returned cursor belongs to reverse history, not forward continuation.
    Empty windows cannot establish exhaustion. This bounded traversal is used
    for reconnect recovery; whole recordings use reverse pagination/spooling.
    """
    start_dt, end_dt = _as_utc(start_dt), _as_utc(end_dt)
    if end_dt <= start_dt:
        return
    current = start_dt.replace(microsecond=0)
    seen_messages = _SeenMessageCache(limit=_HISTORY_SEEN_MESSAGE_LIMIT)
    page_count = raw_record_count = 0
    last_yielded_timestamp: datetime | None = None
    while current <= end_dt:
        if max_pages is not None and page_count >= max_pages:
            log(
                "warning",
                "Kick forward history reached its bounded page limit; stopping.",
            )
            return
        if max_records is not None and raw_record_count >= max_records:
            log(
                "warning",
                "Kick forward history reached its raw-record limit; stopping.",
            )
            return
        page_count += 1
        raw_messages, _cursor = fetch_with_retry(
            partial(
                fetch_validated_page,
                api_client,
                channel_id,
                start_time=format_history_start(current),
            ),
            request,
        )
        if max_records is not None:
            raw_messages = raw_messages[: max_records - raw_record_count]
        raw_record_count += len(raw_messages)
        for timestamp, _index, raw in _ordered_page_messages(raw_messages):
            if timestamp < start_dt or timestamp > end_dt:
                continue
            if (
                last_yielded_timestamp is not None
                and timestamp < last_yielded_timestamp
            ):
                continue
            message_id = _message_identity(raw)
            if message_id is not None and not seen_messages.register(message_id)[0]:
                continue
            yield raw
            last_yielded_timestamp = timestamp
        if end_dt - current < timedelta(seconds=5):
            return
        current += timedelta(seconds=5)

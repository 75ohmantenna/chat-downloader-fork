# SPDX-License-Identifier: MIT

"""Bounded reverse pagination and validation of Kick chat history."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.utils.json_types import JSONDict, JSONList, get_dict, get_list

from .errors import KickError, KickServerError
from .request_retry import fetch_with_retry

if TYPE_CHECKING:
    from collections.abc import Generator
    from threading import Event

    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient

_HISTORY_SEEN_MESSAGE_LIMIT = 10_000


def _as_utc(timestamp: datetime) -> datetime:
    """Return an aware UTC timestamp, treating naive provider values as UTC."""
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _cursor_after(timestamp: datetime) -> str:
    """Return a reverse cursor after the inclusive, second-granular end."""
    delta = _as_utc(timestamp) - datetime(1970, 1, 1, tzinfo=UTC)
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
        + 1_000_000
    )
    return str(microseconds)


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


def iter_reverse_history(  # noqa: C901 — bounded pagination, cancellation and coverage guards
    api_client: KickApiClient,
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    max_pages: int = 100,
    max_records: int = _HISTORY_SEEN_MESSAGE_LIMIT,
    cancelled: Event | None = None,
) -> Generator[JSONDict, None, None]:
    """Yield newest pages first; the owner buffers and sorts before emission.

    Forward responses are capped and cannot establish five-second coverage.
    Empty reverse pages with continuation are followed. Exhausted budgets and
    broken pagination raise rather than silently certifying partial recovery.
    """
    start_dt, end_dt = _as_utc(start_dt), _as_utc(end_dt)
    if end_dt < start_dt:
        return
    cursor: str | None = _cursor_after(end_dt)
    requested_cursors: set[str] = set()
    page_digests: set[bytes] = set()
    seen_messages = _SeenMessageCache(limit=_HISTORY_SEEN_MESSAGE_LIMIT)
    page_count = raw_record_count = 0
    while cursor:
        if cancelled is not None and cancelled.is_set():
            return
        if page_count >= max_pages or raw_record_count >= max_records:
            msg = "Kick live history reached its bounded limit; recovery is incomplete."
            raise KickError(msg)
        if cursor in requested_cursors:
            msg = "Kick live history cursor repeated; recovery is incomplete."
            raise KickError(msg)
        requested_cursors.add(cursor)
        requested_cursor = cursor
        page_count += 1
        raw_messages, cursor = fetch_with_retry(
            partial(
                fetch_validated_page,
                api_client,
                channel_id,
                cursor=cursor,
            ),
            request,
        )
        if cancelled is not None and cancelled.is_set():
            return
        if cursor is not None and cursor.isascii() and cursor.isdigit():
            # Compare decimal strings without accepting arbitrarily large ints.
            current_value, next_value = (
                value.lstrip("0") for value in (requested_cursor, cursor)
            )
            if (
                requested_cursor.isascii()
                and requested_cursor.isdigit()
                and (len(next_value), next_value) >= (len(current_value), current_value)
            ):
                msg = "Kick live history cursor did not move backwards."
                raise KickError(msg)
        if raw_messages:
            digest = hashlib.sha256(
                json.dumps(raw_messages, sort_keys=True).encode("utf-8")
            ).digest()
            if digest in page_digests:
                msg = "Kick live history returned a duplicate page."
                raise KickError(msg)
            page_digests.add(digest)
        truncated = len(raw_messages) > max_records - raw_record_count
        raw_messages = raw_messages[: max_records - raw_record_count]
        raw_record_count += len(raw_messages)
        before_start = False
        for timestamp, _index, raw in _ordered_page_messages(raw_messages):
            if timestamp < start_dt:
                before_start = True
                continue
            if timestamp > end_dt:
                continue
            message_id = _message_identity(raw)
            if message_id is not None and not seen_messages.register(message_id)[0]:
                continue
            yield raw
        if truncated:
            msg = "Kick live history hit its raw-record limit; recovery is incomplete."
            raise KickError(msg)
        if before_start:
            return

# SPDX-License-Identifier: MIT

"""Kick VOD (video-on-demand) chat replay.

Fetches chat messages for a past broadcast by paginating through the
channel's message history and filtering by the VOD's time window.

Reverse cursor pages are buffered in a temporary spool before chronological
emission. Forward start_time responses are short time windows and their cursor
is not a forward continuation token; using it silently loses replay records.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Event
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.utils.time_utils import ensure_seconds, seconds_to_time

from .constants import MESSAGE_GROUPS
from .errors import KickError
from .history import _message_timestamp, fetch_validated_page
from .parsing.messages import parse_chat_message
from .request_retry import fetch_with_retry
from .vod_metadata import _resolve_vod_window, fetch_vod_metadata

_VOD_SPOOL_MEMORY_BYTES = 1024 * 1024

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from .api_client import KickApiClient


class ReplaySource:
    """Cancel buffered retrieval between requests when a deadline closes it."""

    def __init__(
        self,
        channel_id: str,
        start_dt: datetime,
        end_dt: datetime,
        request: ChatRequest,
        *,
        api_client: KickApiClient,
        origin: datetime,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        """Bind cancellation to one buffered replay generator."""
        self.cancelled = Event()
        self.source = _iter_vod_messages(
            channel_id,
            start_dt,
            end_dt,
            request,
            api_client=api_client,
            origin=origin,
            diagnostics=diagnostics,
            cancelled=self.cancelled,
        )

    def __iter__(self) -> ReplaySource:
        """Return this closeable iterator."""
        return self

    def __next__(self) -> JSONDict:
        """Retrieve the next chronological replay record."""
        return next(self.source)

    def close(self) -> None:
        """Cancel pending pagination and close an idle generator immediately."""
        self.cancelled.set()
        try:
            self.source.close()
        except ValueError as error:
            # A deadline can close a generator while its HTTP call is executing.
            if str(error) != "generator already executing":
                raise


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
    video_data = fetch_vod_metadata(api_client, username, video_id, request)
    channel_id, _chatroom_id, title, vod_start_dt, vod_end_dt = _resolve_vod_window(
        video_data, username
    )
    start_dt, end_dt = _apply_request_window(vod_start_dt, vod_end_dt, request)

    log("info", f"VOD time window: {start_dt} to {end_dt}")

    diagnostics: dict[str, object] = {}
    transport = getattr(api_client, "diagnostics", {})
    if isinstance(transport, dict):
        diagnostics["transport"] = transport
    return Chat(
        ReplaySource(
            channel_id,
            start_dt,
            end_dt,
            request,
            api_client=api_client,
            origin=vod_start_dt,
            diagnostics=diagnostics,
        ),
        diagnostics=diagnostics,
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
    """Classify one newest-first record against the selected replay window."""
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
    return parsed, False


def _cursor_after(timestamp: datetime) -> str:
    """Return a reverse cursor after the inclusive, second-granular end."""
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


def _iter_vod_messages(
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    api_client: KickApiClient,
    origin: datetime | None = None,
    diagnostics: dict[str, object] | None = None,
    cancelled: Event | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Replay complete reverse-cursor history and add recording-relative time."""
    state = diagnostics if diagnostics is not None else {}
    state.update(
        protocol="reverse",
        termination_reason="interrupted",
        pages=0,
        raw_records=0,
        emitted_records=0,
        requested_start=start_dt.isoformat(),
        requested_end=end_dt.isoformat(),
    )
    origin = origin if origin is not None else start_dt
    try:
        for parsed in _iter_reverse_vod_messages(
            channel_id,
            start_dt,
            end_dt,
            request,
            api_client=api_client,
            diagnostics=state,
            cancelled=cancelled,
        ):
            timestamp = parsed.get("timestamp")
            if isinstance(timestamp, int):
                offset = (timestamp - int(origin.timestamp() * 1_000_000)) / 1_000_000
                parsed["time_in_seconds"] = offset
                parsed["time_text"] = seconds_to_time(offset)
                state.setdefault("first_timestamp", timestamp)
                state["last_timestamp"] = timestamp
            state["emitted_records"] = cast("int", state["emitted_records"]) + 1
            yield parsed
    except Exception:
        state["termination_reason"] = "error"
        raise


def _iter_reverse_vod_messages(  # noqa: C901 — compatibility protocol guards are cohesive
    channel_id: str,
    start_dt: datetime,
    end_dt: datetime,
    request: ChatRequest,
    *,
    api_client: KickApiClient,
    msg_filter: MessageFilter | None = None,
    diagnostics: dict[str, object] | None = None,
    cancelled: Event | None = None,
) -> Generator[JSONDict, None, None]:
    """Yield replay through Kick's legacy newest-first cursor protocol."""
    state = diagnostics if diagnostics is not None else {}
    if end_dt <= start_dt:
        state["termination_reason"] = "empty_window"
        return
    if msg_filter is None:
        msg_filter = MessageFilter.from_request(MESSAGE_GROUPS, request)

    cursor: str | None = _cursor_after(end_dt)
    done = False
    page_offsets: list[int] = []
    requested_cursors: set[str] = set()
    seen_page_digests: set[bytes] = set()

    seen_messages = _SeenMessageCache(limit=10_000)
    with tempfile.SpooledTemporaryFile(max_size=_VOD_SPOOL_MEMORY_BYTES) as spool:
        while not done:
            if cancelled is not None and cancelled.is_set():
                return
            if cursor is not None:
                if cursor in requested_cursors:
                    msg = "Kick VOD pagination cursor repeated; replay is incomplete."
                    raise KickError(msg)
                requested_cursors.add(cursor)
            requested_cursor = cursor
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
            state["pages"] = cast("int", state.get("pages", 0)) + 1
            state["raw_records"] = cast("int", state.get("raw_records", 0)) + len(
                raw_messages
            )
            if (
                cursor is not None
                and cursor.isascii()
                and cursor.isdigit()
                and requested_cursor
                and requested_cursor.isdigit()
                and int(cursor) >= int(requested_cursor)
            ):
                msg = "Kick reverse history cursor did not move backwards."
                raise KickError(msg)
            if not raw_messages:
                state["empty_pages"] = cast("int", state.get("empty_pages", 0)) + 1
                if cursor:
                    continue
                break
            page_digest = hashlib.sha256(
                json.dumps(raw_messages, sort_keys=True).encode("utf-8")
            ).digest()
            if page_digest in seen_page_digests:
                msg = (
                    "Kick VOD pagination returned a duplicate page; "
                    "replay is incomplete."
                )
                raise KickError(msg)
            seen_page_digests.add(page_digest)

            page_messages: list[JSONDict] = []
            ordered = sorted(
                [raw for raw in raw_messages if isinstance(raw, dict)],
                key=lambda raw: (
                    _message_timestamp(raw) or datetime.min.replace(tzinfo=UTC)
                ),
                reverse=True,
            )
            state["skipped_records"] = (
                cast("int", state.get("skipped_records", 0))
                + len(raw_messages)
                - len(ordered)
            )
            for raw in ordered:
                parsed, msg_done = _classify_message(raw, start_dt, end_dt)
                if msg_done:
                    done = True
                if parsed is None:
                    state["skipped_records"] = (
                        cast("int", state.get("skipped_records", 0)) + 1
                    )
                elif not msg_filter.should_add(parsed):
                    state["filtered_records"] = (
                        cast("int", state.get("filtered_records", 0)) + 1
                    )
                elif not seen_messages.register(str(parsed["message_id"]))[0]:
                    state["duplicate_records"] = (
                        cast("int", state.get("duplicate_records", 0)) + 1
                    )
                else:
                    page_messages.append(cast("JSONDict", parsed))

            if page_messages:
                page_offsets.append(spool.tell())
                spool.write(json.dumps(page_messages).encode("utf-8") + b"\n")
            if not cursor or done:
                break

        state["history_complete"] = True
        emitted = 0
        last_timestamp: int | None = None
        for page_offset in reversed(page_offsets):
            spool.seek(page_offset)
            page_messages = cast("list[JSONDict]", json.loads(spool.readline()))
            for message in reversed(page_messages):
                if cancelled is not None and cancelled.is_set():
                    return
                if request.max_messages is not None and emitted >= request.max_messages:
                    state["termination_reason"] = "message_limit"
                    return
                timestamp = message.get("timestamp")
                if isinstance(timestamp, int):
                    if last_timestamp is not None and timestamp < last_timestamp:
                        msg = (
                            "Kick reverse history pages overlap out of order; "
                            "replay is incomplete."
                        )
                        raise KickError(msg)
                    last_timestamp = timestamp
                emitted += 1
                if request.max_messages is not None and emitted >= request.max_messages:
                    state["termination_reason"] = "message_limit"
                yield message
        state["termination_reason"] = "completed"

# SPDX-License-Identifier: MIT

"""Replay channel history within a VOD (video-on-demand) time window.

Reverse-cursor pages spool temporarily before chronological output. Forward
start_time responses cover short windows; their cursor is not a forward
continuation token and using it silently loses replay records.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from functools import partial
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.utils.time_utils import seconds_to_time

from .constants import MESSAGE_GROUPS
from .errors import KickError
from .history import _cursor_after, _message_timestamp, fetch_validated_page
from .replay_window import (
    _apply_request_window,
    _bump,
    _classify_message,
)
from .request_retry import fetch_with_retry
from .resource_limits import (
    ReplayBudget,
    check_replay_cursor,
    read_replay_record,
    replay_page_digest,
)
from .vod_metadata import _resolve_vod_window, fetch_vod_metadata

_VOD_SPOOL_MEMORY_BYTES = 1024 * 1024

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict, JSONList

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

    def request_stop(self) -> None:
        """Cancel pagination even while a timed worker owns generator advancement."""
        self.cancelled.set()

    def close(self) -> None:
        """Cancel pending pagination and close an idle generator immediately."""
        self.request_stop()
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
    """Paginate the VOD history window in chronological order."""
    diagnostics: dict[str, object] = {}
    video_data = fetch_vod_metadata(
        api_client, username, video_id, request, diagnostics=diagnostics
    )
    channel_id, _chatroom_id, title, vod_start_dt, vod_end_dt = _resolve_vod_window(
        video_data, username
    )
    start_dt, end_dt = _apply_request_window(vod_start_dt, vod_end_dt, request)

    log("info", f"VOD time window: {start_dt} to {end_dt}")

    transport = getattr(api_client, "diagnostics", {})
    if isinstance(transport, dict):
        diagnostics["transport"] = transport
    chat = Chat(
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
    chat._request_window = {
        "start_time": (start_dt - vod_start_dt).total_seconds(),
        "end_time": (end_dt - vod_start_dt).total_seconds(),
        "time_basis": "recording",
    }
    return chat


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
        history_complete=False,
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
        state["history_complete"] = False
        raise
    finally:
        if (
            cancelled is not None
            and cancelled.is_set()
            and state.get("termination_reason")
            not in {"message_limit", "completed", "empty_window"}
        ):
            state["history_complete"] = False


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
    state["history_complete"] = False
    if msg_filter is None:
        msg_filter = MessageFilter.from_request(MESSAGE_GROUPS, request)
    if end_dt <= start_dt:
        state["termination_reason"] = "empty_window"
        return

    started = last_progress = monotonic()
    log("info", "Collecting Kick replay history before chronological output.")
    cursor: str | None = _cursor_after(end_dt)
    done = False
    page_offsets: list[tuple[int, int]] = []
    requested_cursors: set[str] = set()
    seen_page_digests: set[bytes] = set()
    budget = ReplayBudget()

    seen_messages = _SeenMessageCache(limit=10_000)
    with tempfile.SpooledTemporaryFile(max_size=_VOD_SPOOL_MEMORY_BYTES) as spool:
        while not done:
            if cancelled is not None and cancelled.is_set():
                return
            budget.reserve_page(cursor)
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
                cancelled=cancelled,
            )
            if cancelled is not None and cancelled.is_set():
                return
            check_replay_cursor(cursor)
            budget.reserve_records(len(raw_messages))
            _bump(state, "pages")
            _bump(state, "raw_records", len(raw_messages))
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
            now = monotonic()
            if now - last_progress >= 5:
                earliest = None
                for raw in raw_messages:
                    if cancelled is not None and cancelled.is_set():
                        return
                    if isinstance(raw, dict):
                        stamp = _message_timestamp(raw)
                        if stamp is not None:
                            earliest = (
                                stamp if earliest is None else min(earliest, stamp)
                            )
                log(
                    "info",
                    f"Kick replay: {state.get('pages', 0)} pages, "
                    f"{state.get('raw_records', 0)} records collected "
                    f"in {now - started:.1f}s; "
                    f"earliest page timestamp: {earliest}.",
                )
                last_progress = now
            if not raw_messages:
                _bump(state, "empty_pages")
                if cursor:
                    continue
                break
            page_digest = replay_page_digest(raw_messages, cancelled)
            if page_digest is None:
                return
            if page_digest in seen_page_digests:
                msg = (
                    "Kick VOD pagination returned a duplicate page; "
                    "replay is incomplete."
                )
                raise KickError(msg)
            seen_page_digests.add(page_digest)

            page_messages: list[JSONDict] = []
            ordered = _ordered_replay_messages(raw_messages, cancelled)
            if ordered is None:
                return
            malformed = len(raw_messages) - len(ordered)
            _bump(state, "skipped_records", malformed)
            _bump(state, "malformed_object", malformed)
            for raw in ordered:
                if cancelled is not None and cancelled.is_set():
                    return
                parsed, msg_done = _classify_message(raw, start_dt, end_dt, state)
                if msg_done:
                    done = True
                if parsed is None:
                    _bump(state, "skipped_records")
                elif not msg_filter.should_add(parsed):
                    _bump(state, "filtered_records")
                elif not seen_messages.register(str(parsed["message_id"]))[0]:
                    _bump(state, "duplicate_records")
                else:
                    page_messages.append(cast("JSONDict", parsed))

            _bump(state, "selected_records", len(page_messages))
            if page_messages:
                page_offset = spool.tell()
                if not budget.write_page(spool, page_messages, cancelled):
                    return
                page_offsets.append((page_offset, len(page_messages)))
            if not cursor or done:
                break

        if cancelled is not None and cancelled.is_set():
            return
        state["history_complete"] = True
        log(
            "info",
            f"Kick replay history collected in {monotonic() - started:.1f}s; "
            "writing chronological output.",
        )
        emitted = 0
        last_timestamp: int | None = None
        for page_offset, record_count in reversed(page_offsets):
            if cancelled is not None and cancelled.is_set():
                return
            spool.seek(page_offset)
            for _ in range(record_count):
                if cancelled is not None and cancelled.is_set():
                    return
                message = read_replay_record(spool)
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


def _ordered_replay_messages(
    raw_messages: JSONList, cancelled: Event | None
) -> list[JSONDict] | None:
    """Prepare sort keys with a cancellation checkpoint for every record."""
    timestamped: list[tuple[datetime, JSONDict]] = []
    for raw in raw_messages:
        if cancelled is not None and cancelled.is_set():
            return None
        if isinstance(raw, dict):
            timestamped.append(
                (_message_timestamp(raw) or datetime.min.replace(tzinfo=UTC), raw)
            )
    timestamped.sort(key=lambda item: item[0], reverse=True)
    if cancelled is not None and cancelled.is_set():
        return None
    return [raw for _timestamp, raw in timestamped]

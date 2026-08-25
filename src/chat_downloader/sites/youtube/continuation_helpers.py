# SPDX-License-Identifier: MIT

"""Pure helpers for the YouTube chat continuation loop.

Stateless functions and the loop's mutable state model. These carry no
downloader/session dependency and no network I/O, so they are unit-tested in
isolation. The orchestration that wires them together lives in
:mod:`.continuation`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, SupportsIndex, SupportsInt

from chat_downloader.errors import NoContinuation
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.time_utils import seconds_to_time

from .constants_message import _MESSAGE_GROUPS
from .constants_patterns import _YT_HOME

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from .continuations import ContinuationParseResult

_YOUTUBE_POLL_DELAY_FALLBACK_MS = 5000
_YOUTUBE_POLL_DELAY_MIN_MS = 500
_YOUTUBE_POLL_DELAY_MAX_MS = 8000
_PollDelayHint = str | bytes | bytearray | SupportsInt | SupportsIndex | None


@dataclass(slots=True)
class ContinuationLoopState:
    """Mutable state carried between continuation-loop iterations."""

    continuation: str
    click_tracking_params: str | None = None
    # Nonnegative polling position. Presentation timing is derived separately
    # and may be negative for messages in the initial live-chat backlog.
    offset_milliseconds: float | None = None


def build_continuation_params(
    innertube_context: JSONDict,
    state: ContinuationLoopState,
    *,
    is_replay: bool,  # noqa: ARG001 — reserved; continuation body is the same for live and replay
) -> JSONDict:
    """Build the JSON POST body for the next live-chat continuation request."""
    # Shallow-copy context so callers' dict is never mutated between calls.
    context: JSONDict = dict(innertube_context)

    if state.click_tracking_params:
        context["clickTracking"] = {"clickTrackingParams": state.click_tracking_params}

    params: JSONDict = {
        "context": context,
        "continuation": state.continuation,
    }

    # yt-dlp sends playerOffsetMs on follow-up live-chat requests as well as
    # replay requests. Keeping it present once we have an offset makes the
    # polling shape closer to the browser/yt-dlp behavior.
    if state.offset_milliseconds is not None:
        params["currentPlayerState"] = {
            "playerOffsetMs": max(state.offset_milliseconds - 5000, 0),
        }

    return params


def extract_visitor_data(yt_info: JSONDict) -> str | None:
    """Extract the ``visitorData`` token from an API response."""
    visitor_data: str | None = multi_get(yt_info, "responseContext", "visitorData")
    return visitor_data


def get_live_start_time_ms() -> int:
    """Return the wall-clock millisecond baseline for live offset derivation."""
    return int(time.time() * 1000)


def derive_live_offset_milliseconds(
    message: JSONDict,
    live_start_time_ms: int,
) -> int | None:
    """Derive a signed capture-relative offset from a message timestamp.

    yt-dlp computes a live offset by comparing ``timestampUsec`` to a wall-clock
    start time captured when live chat retrieval begins. We use the same idea to
    backfill presentation timing fields for live messages. Negative offsets
    identify messages that arrived in the initial backlog before retrieval
    began; the continuation loop separately keeps ``playerOffsetMs``
    nonnegative.
    """
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, int):
        return None

    timestamp_ms = timestamp // 1000
    return timestamp_ms - live_start_time_ms


def enrich_live_message_timing(
    message: JSONDict,
    live_offset_milliseconds: int | None,
) -> None:
    """Populate time_in_seconds/time_text for live messages when absent."""
    if live_offset_milliseconds is None:
        return
    if "time_in_seconds" in message or "time_text" in message:
        return

    time_in_seconds = live_offset_milliseconds / 1000
    message["time_in_seconds"] = time_in_seconds
    message["time_text"] = seconds_to_time(time_in_seconds)


def update_state_from_result(
    state: ContinuationLoopState,
    cont_result: ContinuationParseResult,
) -> ContinuationLoopState:
    """Advance the loop state using the parsed continuation result."""
    if cont_result.next_continuation is None:
        return state

    raw_entry = cont_result.debug_info.get("continuation_entry")
    # pragma: no cover — when next_continuation is set, debug_info always has
    # continuation_entry; the else-branch is unreachable in normal operation.
    cont_entry = raw_entry if isinstance(raw_entry, dict) else {}  # pragma: no cover
    click_tracking = cont_entry.get("clickTrackingParams") or cont_entry.get(
        "trackingParams",
    )

    return ContinuationLoopState(
        continuation=cont_result.next_continuation,
        click_tracking_params=click_tracking,
        offset_milliseconds=state.offset_milliseconds,
    )


def _resolve_poll_delay_ms(timeout_ms: _PollDelayHint) -> int:
    """Return the safe YouTube chat poll delay in milliseconds."""
    if isinstance(timeout_ms, bool) or timeout_ms is None:
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    try:
        poll_delay_ms = int(timeout_ms)
    except (TypeError, ValueError):
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    if poll_delay_ms < 0:
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    return max(
        _YOUTUBE_POLL_DELAY_MIN_MS,
        min(poll_delay_ms, _YOUTUBE_POLL_DELAY_MAX_MS),
    )


def _select_initial_continuation(
    initial_continuation_info: JSONDict,
    *,
    chat_type: str,
    is_replay: bool,
) -> tuple[str, str]:
    """Select the initial continuation by semantic chat label."""
    if chat_type == "top":
        labels = ("Top chat replay", "Top chat") if is_replay else ("Top chat",)
    else:
        labels = ("Live chat replay", "Live chat") if is_replay else ("Live chat",)

    for label in labels:
        token = initial_continuation_info.get(label)
        if isinstance(token, str) and token:
            return label, token

    available = ", ".join(initial_continuation_info) or "none"
    msg = (
        f"Initial {chat_type} chat continuation could not be found. "
        f"Available continuation labels: {available}"
    )
    raise NoContinuation(msg)


def _build_continuation_urls(
    continuation: str,
    api_key: str,
    *,
    is_replay: bool,
) -> tuple[str, str]:
    """Return (init_page, continuation_url) for the chat loop."""
    api_type = "live_chat_replay" if is_replay else "live_chat"
    init_page = f"{_YT_HOME}/{api_type}?continuation={continuation}"
    continuation_url = f"{_YT_HOME}/youtubei/v1/live_chat/get_{api_type}?key={api_key}"
    return init_page, continuation_url


def _build_message_filters(
    params: ChatRequest,
    *,
    is_replay: bool,
    start_time: float | None,
    end_time: float | None,
    offset: float | None,
) -> tuple[MessageFilter, TimeRangeFilter | None]:
    """Assemble the message-group filter and optional replay time filter."""
    msg_filter = MessageFilter.from_request(_MESSAGE_GROUPS, params)
    time_filter = (
        TimeRangeFilter(
            start_time,
            end_time,
            offset=offset,
            skip_mode="first_page",
        )
        if is_replay
        else None
    )
    return msg_filter, time_filter

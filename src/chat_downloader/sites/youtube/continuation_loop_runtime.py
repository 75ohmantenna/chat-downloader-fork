# SPDX-License-Identifier: MIT

"""Continuation loop runtime helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.time_utils import seconds_to_time

from .continuation_loop_state import ContinuationLoopState

if TYPE_CHECKING:
    from .continuations import ContinuationParseResult


def build_continuation_params(
    innertube_context: dict[str, Any],
    state: ContinuationLoopState,
    is_replay: bool,  # noqa: ARG001 — reserved; continuation body is the same for live and replay
) -> dict[str, Any]:
    """Build the JSON POST body for the next live-chat continuation request."""
    # Shallow-copy context so callers' dict is never mutated between calls.
    context: dict[str, Any] = dict(innertube_context)

    if state.click_tracking_params:
        context["clickTracking"] = {"clickTrackingParams": state.click_tracking_params}

    params: dict[str, Any] = {
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


def extract_visitor_data(yt_info: dict[str, Any]) -> str | None:
    """Extract the ``visitorData`` token from an API response."""
    visitor_data: str | None = multi_get(yt_info, "responseContext", "visitorData")
    return visitor_data


def get_live_start_time_ms() -> int:
    """Return the wall-clock millisecond baseline for live offset derivation."""
    return int(time.time() * 1000)


def derive_live_offset_milliseconds(
    message: dict[str, Any],
    live_start_time_ms: int,
) -> int | None:
    """Derive a rolling live offset from a message timestamp.

    yt-dlp computes a live offset by comparing ``timestampUsec`` to a wall-clock
    start time captured when live chat retrieval begins. We use the same idea to
    improve follow-up ``playerOffsetMs`` requests and to backfill presentation
    timing fields for live messages.
    """
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, int):
        return None

    timestamp_ms = timestamp // 1000
    return max(timestamp_ms - live_start_time_ms, 0)


def enrich_live_message_timing(
    message: dict[str, Any],
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

    cont_entry: dict[str, Any] = cont_result.debug_info.get("continuation_entry") or {}
    click_tracking = cont_entry.get("clickTrackingParams") or cont_entry.get(
        "trackingParams",
    )

    return ContinuationLoopState(
        continuation=cont_result.next_continuation,
        click_tracking_params=click_tracking,
        offset_milliseconds=state.offset_milliseconds,
    )

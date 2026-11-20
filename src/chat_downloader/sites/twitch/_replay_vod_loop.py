# SPDX-License-Identifier: MIT

"""VOD replay loop helpers: offset/filter setup and empty-page logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chat_downloader.debugging import log
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.utils.time_utils import ensure_seconds

from .constants import MESSAGE_GROUPS

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest


@dataclass(frozen=True, slots=True)
class _VodLoopPlan:
    content_offset_seconds: float
    offset: float
    msg_filter: MessageFilter
    time_filter: TimeRangeFilter


def _clip_time_bounds(
    request: ChatRequest, duration: float | None
) -> tuple[float, float | None]:
    """Resolve the effective clip-relative start and end bounds."""
    start_time = max(0.0, ensure_seconds(request.start_time, 0))
    end_time = ensure_seconds(request.end_time, duration)
    if duration is not None:
        end_time = min(end_time, duration)
    return start_time, end_time


def _init_vod_loop(
    request: ChatRequest, max_duration: float | None, offset: float | None
) -> _VodLoopPlan:
    """Resolve start/end offsets and build the message/time filters."""
    start_time = ensure_seconds(request.start_time, 0)
    if offset is None:
        offset = 0.0
        end_time = ensure_seconds(request.end_time)
        content_offset_seconds = (
            start_time if max_duration is None else min(start_time, max_duration)
        )
    else:
        start_time, end_time = _clip_time_bounds(request, max_duration)
        seek_time = (
            start_time if max_duration is None else min(start_time, max_duration)
        )
        content_offset_seconds = seek_time + offset
    msg_filter = MessageFilter.from_request(MESSAGE_GROUPS, request)
    time_filter = TimeRangeFilter(start_time, end_time, skip_mode="always")
    return _VodLoopPlan(content_offset_seconds, offset, msg_filter, time_filter)


def _classify_empty_page(
    *,
    consecutive: int,
    max_empty: int,
    has_next_page: bool,
    vod_id: str,
) -> Literal["break", "continue"]:
    """Decide whether an empty-edges page ends or continues pagination."""
    if consecutive >= max_empty:
        log(
            "warning",
            f"VOD {vod_id}: {max_empty} consecutive empty pages with "
            "hasNextPage=true and no cursor advance; stopping pagination "
            "to avoid an infinite loop.",
        )
        return "break"
    return "continue" if has_next_page else "break"

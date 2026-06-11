# SPDX-License-Identifier: MIT

"""Seam unit tests for the VOD-loop helpers in twitch/_replay_vod_loop.py."""

from __future__ import annotations

import dataclasses

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.twitch._replay_vod_loop import (
    _classify_empty_page,
    _init_vod_loop,
    _VodLoopPlan,
)

# ── _VodLoopPlan ──────────────────────────────────────────────────────────────


def test_vod_loop_plan_is_frozen() -> None:
    mf = MessageFilter([], None, [])
    tf = TimeRangeFilter(None, None, skip_mode="always")
    plan = _VodLoopPlan(
        content_offset_seconds=10.0,
        offset=0.0,
        msg_filter=mf,
        time_filter=tf,
    )
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        plan.content_offset_seconds = 99.0  # type: ignore[misc]


def test_vod_loop_plan_fields_round_trip() -> None:
    mf = MessageFilter([], None, [])
    tf = TimeRangeFilter(None, None, skip_mode="always")
    plan = _VodLoopPlan(
        content_offset_seconds=5.5,
        offset=2.0,
        msg_filter=mf,
        time_filter=tf,
    )
    assert plan.content_offset_seconds == 5.5
    assert plan.offset == 2.0
    assert plan.msg_filter is mf
    assert plan.time_filter is tf


# ── _init_vod_loop ────────────────────────────────────────────────────────────


def test_init_vod_loop_no_offset_uses_start_time_as_content_offset() -> None:
    request = ChatRequest(
        url="https://www.twitch.tv/videos/1",
        max_attempts=1,
        start_time=30.0,
        message_groups=["messages"],
    )
    plan = _init_vod_loop(request, max_duration=None, offset=None)
    assert plan.content_offset_seconds == 30.0
    assert plan.offset == 0.0


def test_init_vod_loop_no_offset_clamps_to_max_duration() -> None:
    request = ChatRequest(
        url="https://www.twitch.tv/videos/1",
        max_attempts=1,
        start_time=120.0,
        message_groups=["messages"],
    )
    plan = _init_vod_loop(request, max_duration=60.0, offset=None)
    assert plan.content_offset_seconds == 60.0


def test_init_vod_loop_with_explicit_offset_adds_to_start_time() -> None:
    request = ChatRequest(
        url="https://www.twitch.tv/videos/1",
        max_attempts=1,
        start_time=10.0,
        message_groups=["messages"],
    )
    plan = _init_vod_loop(request, max_duration=None, offset=20.0)
    assert plan.content_offset_seconds == 30.0
    assert plan.offset == 20.0


def test_init_vod_loop_returns_correct_filter_types() -> None:
    request = ChatRequest(
        url="https://www.twitch.tv/videos/1",
        max_attempts=1,
        message_groups=["messages"],
    )
    plan = _init_vod_loop(request, max_duration=None, offset=None)
    assert isinstance(plan.msg_filter, MessageFilter)
    assert isinstance(plan.time_filter, TimeRangeFilter)


def test_init_vod_loop_zero_start_time_no_offset() -> None:
    request = ChatRequest(
        url="https://www.twitch.tv/videos/1",
        max_attempts=1,
        start_time=0.0,
        message_groups=["messages"],
    )
    plan = _init_vod_loop(request, max_duration=None, offset=None)
    assert plan.content_offset_seconds == 0.0
    assert plan.offset == 0.0


# ── _classify_empty_page ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("consecutive", "max_empty", "has_next_page", "expected"),
    [
        (3, 3, True, "break"),  # consecutive == max_empty → stop
        (4, 3, True, "break"),  # consecutive > max_empty → stop
        (2, 3, True, "continue"),  # under limit, hasNextPage → continue
        (2, 3, False, "break"),  # under limit, no next page → stop
        (0, 3, True, "continue"),  # fresh start
        (0, 3, False, "break"),  # no next page even at start
    ],
)
def test_classify_empty_page(
    consecutive: int,
    max_empty: int,
    has_next_page: bool,
    expected: str,
) -> None:
    result = _classify_empty_page(
        consecutive=consecutive,
        max_empty=max_empty,
        has_next_page=has_next_page,
        vod_id="vod123",
    )
    assert result == expected

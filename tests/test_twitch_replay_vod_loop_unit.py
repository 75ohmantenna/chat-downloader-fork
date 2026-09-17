# SPDX-License-Identifier: MIT

"""Boundary tests for the VOD-loop helpers."""

from __future__ import annotations

import pytest

from chat_downloader.sites.twitch._replay_vod_loop import (
    _classify_empty_page,
    _init_vod_loop,
)
from tests.twitch_third_helpers import chat_request


@pytest.mark.parametrize(
    ("start", "max_duration", "offset", "content_offset", "expected_offset"),
    [
        (30.0, None, None, 30.0, 0.0),
        (120.0, 60.0, None, 60.0, 0.0),
        (10.0, None, 20.0, 30.0, 20.0),
        (0.0, None, None, 0.0, 0.0),
    ],
    ids=["start-time", "duration-clamp", "explicit-offset", "zero-start"],
)
def test_init_vod_loop_offsets(
    start, max_duration, offset, content_offset, expected_offset
):
    request = chat_request(
        url="https://www.twitch.tv/videos/1", max_attempts=1, start_time=start
    )
    plan = _init_vod_loop(request, max_duration=max_duration, offset=offset)
    assert plan.content_offset_seconds == content_offset
    assert plan.offset == expected_offset


@pytest.mark.parametrize(
    ("consecutive", "max_empty", "has_next_page", "expected"),
    [
        (3, 3, True, "break"),
        (4, 3, True, "break"),
        (2, 3, True, "continue"),
        (2, 3, False, "break"),
        (0, 3, True, "continue"),
        (0, 3, False, "break"),
    ],
    ids=[
        "at-limit",
        "over-limit",
        "under-limit",
        "terminal",
        "fresh",
        "fresh-terminal",
    ],
)
def test_classify_empty_page(consecutive, max_empty, has_next_page, expected) -> None:
    assert (
        _classify_empty_page(
            consecutive=consecutive,
            max_empty=max_empty,
            has_next_page=has_next_page,
            vod_id="vod123",
        )
        == expected
    )

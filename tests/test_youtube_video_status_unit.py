# SPDX-License-Identifier: MIT

"""Focused contracts for YouTube video-status helpers."""

from __future__ import annotations

import pytest

from chat_downloader.sites.youtube.video_status import parse_video_details
from chat_downloader.sites.youtube.video_status_helpers import _derive_duration

_DURATION_CASES = "first_format,video_details,player_renderer,start,end,want"


@pytest.mark.parametrize(
    _DURATION_CASES,
    [
        # approxDurationMs (ms string) → seconds
        ({"approxDurationMs": "5000"}, {}, {}, None, None, 5.0),
        # lengthSeconds in video_details
        ({}, {"lengthSeconds": "30"}, {}, None, None, 30.0),
        # lengthSeconds in player_renderer
        ({}, {}, {"lengthSeconds": "42"}, None, None, 42.0),
        # live start/end fallback (values in microseconds → seconds)
        ({}, {}, {}, 1_000_000.0, 7_000_000.0, 6.0),
        # nothing available
        ({}, {}, {}, None, None, None),
    ],
)
def test_derive_duration(
    first_format: dict,
    video_details: dict,
    player_renderer: dict,
    start: float | None,
    end: float | None,
    want: float | None,
) -> None:
    result = _derive_duration(first_format, video_details, player_renderer, start, end)
    assert result == want


@pytest.mark.parametrize(
    ("streaming", "duration"),
    [
        ({"formats": [1]}, 60),
        ({"adaptiveFormats": [1], "formats": [{"approxDurationMs": "70000"}]}, 70),
        ("unexpected", 60),
    ],
)
def test_video_details_fall_back_from_invalid_format_metadata(streaming, duration):
    detail = parse_video_details(
        {
            "streamingData": streaming,
            "microformat": {"playerMicroformatRenderer": {"liveBroadcastDetails": 1}},
            "videoDetails": {"videoId": "abc", "lengthSeconds": "60"},
        },
        {},
        "abc",
    )
    assert detail.duration == duration

# SPDX-License-Identifier: MIT

"""Seam tests for helpers extracted from video_status in W1."""

from __future__ import annotations

import pytest

from chat_downloader.sites.youtube.video_status_helpers import (
    _derive_duration,
    _log_player_response_shape,
)

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


def test_log_player_response_shape_no_optional_keys() -> None:
    _log_player_response_shape({}, {}, {}, {})


def test_log_player_response_shape_with_live_broadcast_details() -> None:
    _log_player_response_shape(
        {},
        {},
        {},
        {"liveBroadcastDetails": {"liveBroadcastContent": "live"}},
    )


def test_log_player_response_shape_with_broadcast_details_no_content() -> None:
    _log_player_response_shape(
        {},
        {},
        {},
        {"liveBroadcastDetails": {}},
    )


def test_log_player_response_shape_with_live_streaming_details() -> None:
    _log_player_response_shape(
        {"liveStreamingDetails": {"activeLiveChatId": "abc"}},
        {},
        {},
        {},
    )

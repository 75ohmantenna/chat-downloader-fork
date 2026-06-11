# SPDX-License-Identifier: MIT

"""YouTube video status parsing and serialization."""

from __future__ import annotations

import dataclasses
from typing import Any

from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.time_utils import parse_iso8601

from .video_status_helpers import (
    _derive_duration,
    _determine_status,
    _determine_video_type,
    _extract_continuation_info,
    _log_player_response_shape,
)
from .video_status_models import VideoDetails


def parse_video_details(
    player_response_info: dict[str, Any],
    yt_initial_data: dict[str, Any],
    video_id: str,
    video_type: str = "video",
) -> VideoDetails:
    """Parse player-response and initial-data into :class:`VideoDetails`."""
    from chat_downloader.errors import ParsingError

    streaming_data = player_response_info.get("streamingData") or {}
    first_format: dict[str, Any] = (
        multi_get(streaming_data, "adaptiveFormats", 0)
        or multi_get(streaming_data, "formats", 0)
        or {}
    )
    player_renderer: dict[str, Any] = (
        multi_get(
            player_response_info, "microformat", "playerMicroformatRenderer"
        )
        or {}
    )
    live_details: dict[str, Any] = (
        player_renderer.get("liveBroadcastDetails") or {}
    )
    video_details: dict[str, Any] = (
        player_response_info.get("videoDetails") or {}
    )

    pr_video_id = video_details.get("videoId")
    if pr_video_id and pr_video_id != video_id and video_type != "clip":
        msg = (
            f"YouTube returned player response for wrong video. "
            f"Requested: {video_id}, Got: {pr_video_id}"
        )
        raise ParsingError(msg)

    _log_player_response_shape(
        player_response_info, video_details, live_details, player_renderer
    )

    title = video_details.get("title")
    author = video_details.get("author")
    author_id = video_details.get("channelId")
    original_video_id = video_details.get("videoId")

    resolved_type, clip_start_time, clip_end_time = _determine_video_type(
        player_response_info,
        video_details,
    )

    start_timestamp = live_details.get("startTimestamp")
    end_timestamp = live_details.get("endTimestamp")
    start_time = parse_iso8601(start_timestamp) if start_timestamp else None
    end_time = parse_iso8601(end_timestamp) if end_timestamp else None

    duration = _derive_duration(
        first_format, video_details, player_renderer, start_time, end_time
    )
    continuation_info = _extract_continuation_info(yt_initial_data)
    status = _determine_status(video_details, live_details)

    return VideoDetails(
        title=title,
        author=author,
        author_id=author_id,
        original_video_id=original_video_id,
        video_type=resolved_type,
        status=status,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        continuation_info=continuation_info,
        clip_start_time=clip_start_time,
        clip_end_time=clip_end_time,
    )


def video_details_to_dict(details: VideoDetails) -> dict[str, Any]:
    """Convert :class:`VideoDetails` to a plain dictionary."""
    return dataclasses.asdict(details)


__all__ = ["parse_video_details", "video_details_to_dict"]

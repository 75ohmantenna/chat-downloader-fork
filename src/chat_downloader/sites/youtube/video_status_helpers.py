# SPDX-License-Identifier: MIT

"""Helpers for parsing YouTube video status metadata."""

from typing import Any, Literal

from chat_downloader.debugging import logger
from chat_downloader.utils.conversion_utils import float_or_none

from .helpers import extract_chat_submenu_continuations


def _determine_video_type(
    player_response_info: dict[str, Any],
    video_details: dict[str, Any],
) -> tuple[str, float | None, float | None]:
    """Resolve video type and optional clip offsets."""
    clip_config = player_response_info.get("clipConfig")
    if clip_config:
        clip_start = (
            float_or_none(clip_config.get("startTimeMs", 0)) or 0.0
        ) / 1e3
        clip_end = (float_or_none(clip_config.get("endTimeMs", 0)) or 0.0) / 1e3
        return "clip", clip_start, clip_end
    if not video_details.get("isLiveContent"):
        return "premiere", None, None
    return "video", None, None


def _determine_status(
    video_details: dict[str, Any],
    live_details: dict[str, Any],
) -> Literal["post_live", "live", "upcoming", "was_live", "not_live", "past"]:
    """Resolve broadcast status from player response metadata."""
    is_live = video_details.get("isLive")
    if is_live is None:
        is_live = live_details.get("isLiveNow")
    live_content = video_details.get("isLiveContent")
    is_upcoming = video_details.get("isUpcoming")
    post_live = video_details.get("isPostLiveDvr")

    logger.debug(
        f"Status detection - isLive: {is_live}, "
        f"isLiveNow: {live_details.get('isLiveNow')}, "
        f"isLiveContent: {live_content}, "
        f"isUpcoming: {is_upcoming}, "
        f"isPostLiveDvr: {post_live}",
    )

    if post_live:
        return "post_live"
    if is_live:
        return "live"
    if is_upcoming:
        return "upcoming"
    if live_content:
        return "was_live"
    if is_live is False or live_content is False:
        has_broadcast_details = bool(
            live_details.get("startTimestamp")
            or live_details.get("endTimestamp"),
        )
        return "was_live" if has_broadcast_details else "not_live"
    return "past"


def _extract_continuation_info(
    yt_initial_data: dict[str, Any],
) -> dict[str, str]:
    """Extract mapping of chat menu label to continuation token."""
    fallback_info = yt_initial_data.get("_chat_downloader_continuation_info")
    if isinstance(fallback_info, dict):
        return {
            key: value
            for key, value in fallback_info.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    return extract_chat_submenu_continuations(yt_initial_data)

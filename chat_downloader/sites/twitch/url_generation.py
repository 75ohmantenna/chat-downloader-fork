# SPDX-License-Identifier: MIT

"""Twitch URL generation helpers."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .constants import TWITCH_HOME, TWITCH_VIDEOS
from .discovery import get_top_livestreams, get_user_clips, get_user_videos
from .graphql_client import _download_gql
from .parsing.messages import _parse_user

if TYPE_CHECKING:
    from collections.abc import Generator

    from .extractor import TwitchChatDownloader


def generate_urls(
    downloader: TwitchChatDownloader,
    livestream_limit: int,
    vod_limit: int,
    clip_limit: int,
) -> Generator[str, None, None]:
    """Generate livestream, VOD, and clip URLs from Twitch discovery data."""
    num_vods = (
        math.ceil(vod_limit / livestream_limit)
        if livestream_limit > 0
        else vod_limit
    )
    num_clips = (
        math.ceil(clip_limit / livestream_limit)
        if livestream_limit > 0
        else clip_limit
    )

    livestreams = get_top_livestreams(
        downloader._session_post,
        _download_gql,
        livestream_limit,
    )
    for livestream in livestreams:
        broadcaster = _parse_user(livestream.get("broadcaster"))
        name = broadcaster.get("name")
        if not name:
            continue

        yield f"{TWITCH_HOME}/{name}"

        for vod in get_user_videos(
            downloader._session_post,
            _download_gql,
            name,
            num_vods,
        ):
            vod_id = vod.get("id")
            if vod_id:
                yield f"{TWITCH_VIDEOS}/{vod_id}"

        for clip in get_user_clips(
            downloader._session_post,
            _download_gql,
            name,
            num_clips,
        ):
            clip_url = clip.get("url")
            if clip_url:
                yield clip_url

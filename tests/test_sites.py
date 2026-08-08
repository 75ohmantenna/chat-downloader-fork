# SPDX-License-Identifier: MIT

from __future__ import annotations

import itertools

# Network-dependent integration tests (YouTube API).
import pytest

from chat_downloader import ChatDownloader
from chat_downloader.sites import YouTubeChatDownloader

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_live,
    pytest.mark.timeout(75),
]


def test_youtube_channel_discovery() -> None:
    """Resolve supported channel URL forms to concrete live video records."""
    max_videos = 50
    downloader = ChatDownloader()
    try:
        youtube = downloader.create_session(YouTubeChatDownloader)
        tests = [
            {
                "id": "UCwobzUc3z-0PrFpoRxNszXQ",
                "type": "channel_id",
            },
            {
                "id": "LofiGirl",
                "type": "custom_username",
            },
            {
                "id": "LofiGirl",
                "type": "handle",
            },
        ]

        for test in tests:
            videos = list(
                itertools.islice(
                    youtube.get_user_videos(
                        **{test["type"]: test["id"], "video_type": "live"}
                    ),
                    max_videos,
                )
            )
            assert videos
            assert all(video.get("video_id") for video in videos)
    finally:
        downloader.close()

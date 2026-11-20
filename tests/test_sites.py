# SPDX-License-Identifier: MIT

from __future__ import annotations

import itertools
import re

# Network-dependent integration tests (YouTube API).
import pytest

from chat_downloader import ChatDownloader
from chat_downloader.sites import YouTubeChatDownloader

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_live,
    pytest.mark.timeout(75),
]


@pytest.mark.parametrize(
    ("selector", "channel"),
    [
        ("channel_id", "UCSJ4gkVC6NrvII8umztf0Ow"),
        ("user_id", "YellowBrickCinema"),
        ("custom_username", "LofiGirl"),
        ("handle", "LofiGirl"),
    ],
    ids=["channel-id", "legacy-user", "custom-url", "handle"],
)
def test_youtube_channel_discovery(selector: str, channel: str) -> None:
    """Read a bounded sample from each alias's Streams tab."""
    downloader = ChatDownloader()
    try:
        youtube = downloader.create_session(YouTubeChatDownloader)
        assert isinstance(youtube, YouTubeChatDownloader)
        videos = list(
            itertools.islice(
                youtube.get_user_videos(**{selector: channel}, video_type="live"),
                5,
            )
        )
    finally:
        downloader.close()
    assert videos, "Streams tab yielded no video records."
    for video in videos:
        assert re.fullmatch(r"[0-9A-Za-z_-]{11}", video["video_id"])
        assert video["title"].strip()
        assert video["video_type"] in {"DEFAULT", "LIVE", "UPCOMING"}

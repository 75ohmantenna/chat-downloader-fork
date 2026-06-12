# SPDX-License-Identifier: MIT

"""Discovery helpers and test fixtures for YouTube discovery mixin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.utils.dict_utils import multi_get

from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _LIVE_PLAYLIST_URL,
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .discovery_channels_runtime_iteration import get_user_videos

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


def _iter_playlist_urls(content: Any) -> Iterator[str]:
    """Yield playlist URLs from a rendered discovery tab content tree."""
    if isinstance(content, dict):
        shelf = content.get("shelfRenderer")
        if isinstance(shelf, dict):
            url = (
                shelf.get("endpoint", {})
                .get("commandMetadata", {})
                .get("webCommandMetadata", {})
                .get("url")
            )
            if isinstance(url, str) and url.startswith("/playlist?"):
                yield _YT_HOME + url

        for value in content.values():
            yield from _iter_playlist_urls(value)
        return

    if isinstance(content, list):
        for item in content:
            yield from _iter_playlist_urls(item)


def _iter_video_ids(content: Any) -> Iterator[str]:
    """Yield video IDs from a rendered discovery tab content tree."""
    if isinstance(content, dict):
        video = content.get("videoRenderer")
        if isinstance(video, dict):
            video_id = video.get("videoId")
            if isinstance(video_id, str) and video_id:
                yield video_id

        for value in content.values():
            yield from _iter_video_ids(value)
        return

    if isinstance(content, list):
        for item in content:
            yield from _iter_video_ids(item)


def _get_rendered_content(yt_info: dict[str, Any], tab_index: int = 0) -> Any:
    """Extract rendered content from YouTube info."""
    return multi_get(
        yt_info,
        "contents",
        "twoColumnBrowseResultsRenderer",
        "tabs",
        tab_index,
        "tabRenderer",
        "content",
        "sectionListRenderer",
        "contents",
        0,
        "itemSectionRenderer",
        "contents",
        0,
        default={},
    )


class YouTubeDiscoveryHelpersMixin:
    """Shared discovery helpers for rendered content and test items."""

    def generate_urls(self, **kwargs: Any) -> Iterator[str]:  # noqa: ARG002 — base class contract from BaseChatDownloader
        """Generate URLs for testing purposes."""
        items = self._get_testing_items()

        for item in items:
            yield f"{_YT_HOME}/watch?v={item['video_id']}"

    def _get_testing_items(self) -> Iterator[dict[str, Any]]:
        """Get testing items from live playlist."""
        params = {"max_attempts": 10}

        yt_initial_data, _, _ = _get_initial_info(
            _LIVE_PLAYLIST_URL,
            cast("YouTubeDownloaderProto", self)._session_get,
            params,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )

        tabs = yt_initial_data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"]
        tab_content = tabs[0]["tabRenderer"]["content"]

        yielded_video_ids: set[str] = set()
        for video_id in _iter_video_ids(tab_content):
            if video_id in yielded_video_ids:
                continue
            yielded_video_ids.add(video_id)
            yield {"video_id": video_id}

        for playlist_url in _iter_playlist_urls(tab_content):
            yield from cast("YouTubeDownloaderProto", self).get_playlist_items(
                playlist_url
            )

    def get_user_videos(
        self,
        channel_id: str | None = None,
        user_id: str | None = None,
        custom_username: str | None = None,
        handle: str | None = None,
        video_type: str = "videos",
        params: ChatRequest | dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Retrieve all videos listed on the user's channel."""
        if params is None:
            request = None
        else:
            request = cast("YouTubeDownloaderProto", self)._coerce_chat_request(params)
        yield from get_user_videos(
            self,
            channel_id=channel_id,
            user_id=user_id,
            custom_username=custom_username,
            handle=handle,
            video_type=video_type,
            params=request,
        )

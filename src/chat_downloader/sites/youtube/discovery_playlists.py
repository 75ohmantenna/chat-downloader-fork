# SPDX-License-Identifier: MIT

"""Playlist discovery mixin for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.utils.json_types import dig, get_dict, get_list, get_str

from .client_context import _get_innertube_context
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .discovery import _report_discovery_drift
from .helpers import (
    _extract_browse_continuation_token_from_item,
    _extract_browse_continuation_token_from_response,
    _fetch_browse_continuation,
    require_innertube_api_key,
)
from .parsing.message_items_video import _parse_video

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict, JSONList

    from ._protocols import YouTubeDownloaderProto


def _playlist_section_items(content: JSONDict) -> JSONList:
    """Flatten only playlist list containers, excluding menus and sidebars."""
    for key in (
        "sectionListRenderer",
        "itemSectionRenderer",
        "playlistVideoListRenderer",
    ):
        renderer = get_dict(content, key)
        if renderer:
            items: JSONList = []
            for item in get_list(renderer, "contents"):
                if isinstance(item, dict):
                    items.extend(_playlist_section_items(item))
            return items
    return [content]


def _get_playlist_initial_items(initial: JSONDict) -> JSONList:
    """Select the playlist tab and retain all legacy or modern list items."""
    tabs = dig(initial, "contents", "twoColumnBrowseResultsRenderer", "tabs")
    if not isinstance(tabs, list) or not tabs:
        return []
    renderers = [get_dict(tab, "tabRenderer") for tab in tabs if isinstance(tab, dict)]
    selected = next((tab for tab in renderers if tab.get("selected")), None)
    tab = selected if selected is not None else next(iter(renderers), {})
    return _playlist_section_items(get_dict(tab, "content"))


def _extract_playlist_items(
    items: JSONList,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return video dicts and next continuation token from a playlist page."""
    videos: list[dict[str, Any]] = []
    token: str | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        vid = get_dict(item, "playlistVideoRenderer")
        lockup = get_dict(item, "lockupViewModel")
        continuation = _extract_browse_continuation_token_from_item(item)
        if vid:
            videos.append(_parse_video(vid))
        elif get_str(lockup, "contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            parsed = _parse_video({"lockupViewModel": lockup})
            if isinstance(parsed.get("video_id"), str) and parsed["video_id"]:
                videos.append(parsed)
        elif continuation:
            token = continuation
    _report_discovery_drift(items)
    return videos, token


class YouTubePlaylistDiscoveryMixin:
    """Methods for playlist video enumeration."""

    def get_playlist_items(
        self,
        playlist_url: str,
        params: ChatRequest | dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Get items from a YouTube playlist."""
        from chat_downloader.models import ChatRequest as ChatRequestModel

        if params is None:
            request = ChatRequestModel()
        elif isinstance(params, ChatRequestModel):
            request = params
        else:
            request = ChatRequestModel.from_kwargs(**params)

        proto = cast("YouTubeDownloaderProto", self)
        yt_initial_data, ytcfg, _ = _get_initial_info(
            playlist_url,
            proto._session_get,
            request,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )

        api_key = require_innertube_api_key(ytcfg)
        continuation_url = f"{_YT_HOME}/youtubei/v1/browse?key={api_key}"
        continuation_params: dict[str, Any] = {"context": _get_innertube_context(ytcfg)}

        first_items = _get_playlist_initial_items(yt_initial_data)
        videos, continuation = _extract_playlist_items(first_items)
        yield from videos

        seen_continuations: set[str] = set()
        while continuation:
            items_result, yt_info = _fetch_browse_continuation(
                proto,
                continuation,
                continuation_url,
                continuation_params,
                ytcfg,
                request,
                seen_continuations,
            )
            if items_result is None and yt_info is None:
                break
            if not items_result:
                continuation = (
                    _extract_browse_continuation_token_from_response(yt_info)
                    if yt_info
                    else None
                )
                continue
            videos, continuation = _extract_playlist_items(items_result)
            yield from videos

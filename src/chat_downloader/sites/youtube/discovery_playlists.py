# SPDX-License-Identifier: MIT

"""Playlist discovery mixin for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.utils.dict_utils import multi_get

from .client_context import _get_innertube_context
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .discovery_helpers import _get_rendered_content
from .helpers import (
    _extract_browse_continuation_token_from_response,
    _fetch_browse_continuation,
    require_innertube_api_key,
)
from .parsing.message_items_video import _parse_video

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


def _extract_playlist_items(
    items: list[Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return video dicts and next continuation token from a playlist page."""
    videos: list[dict[str, Any]] = []
    token: str | None = None
    for item in items:
        vid = item.get("playlistVideoRenderer")
        cont_item = item.get("continuationItemRenderer")
        if vid:
            videos.append(_parse_video(vid))
        elif cont_item:
            token = multi_get(
                cont_item,
                "continuationEndpoint",
                "continuationCommand",
                "token",
            )
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

        page_contents = _get_rendered_content(yt_initial_data)

        api_key = require_innertube_api_key(ytcfg)
        continuation_url = f"{_YT_HOME}/youtubei/v1/browse?key={api_key}"
        continuation_params: dict[str, Any] = {"context": _get_innertube_context(ytcfg)}

        first_items: list[Any] = (
            multi_get(page_contents, "playlistVideoListRenderer", "contents") or []
        )
        videos, continuation = _extract_playlist_items(first_items)
        yield from videos

        seen_continuations: set[str] = set()
        while continuation:
            items_result, yt_info = _fetch_browse_continuation(
                proto,
                continuation,
                continuation_url,
                continuation_params,
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

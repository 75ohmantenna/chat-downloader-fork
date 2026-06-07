# SPDX-License-Identifier: MIT

"""Playlist discovery mixin for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.utils.dict_utils import multi_get

from ._protocols import YouTubeDownloaderProto
from .client_context import _get_innertube_context
from .client_requests_continuation import _get_continuation_info
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .helpers import _extract_browse_continuation_token_from_response
from .parsing.messages import _parse_video

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest


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

        proto = cast(YouTubeDownloaderProto, self)
        yt_initial_data, ytcfg, _ = _get_initial_info(
            playlist_url,
            proto._session_get,
            request,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )

        page_contents = proto._get_rendered_content(yt_initial_data)

        api_key = ytcfg.get("INNERTUBE_API_KEY")
        continuation_url = f"{_YT_HOME}/youtubei/v1/browse?key={api_key}"

        continuation_params: dict[str, Any] = {
            "context": _get_innertube_context(ytcfg)
        }
        continuation: str | None = None
        first_time = True
        seen_continuations: set[str] = set()
        while True:
            yt_info = None
            if first_time:
                items = multi_get(
                    page_contents,
                    "playlistVideoListRenderer",
                    "contents",
                )
                first_time = False
            else:
                if continuation in seen_continuations:
                    log(
                        "debug",
                        "Detected YouTube playlist continuation loop; "
                        "assuming end of playlist.",
                    )
                    break
                if continuation:
                    seen_continuations.add(continuation)
                continuation_params["continuation"] = continuation
                yt_info = _get_continuation_info(
                    continuation_url,
                    proto._session_post,
                    request,
                    require_live_chat_continuation=False,
                    json=continuation_params,
                )
                items = multi_get(
                    yt_info,
                    "onResponseReceivedActions",
                    0,
                    "appendContinuationItemsAction",
                    "continuationItems",
                ) or multi_get(
                    yt_info,
                    "onResponseReceivedEndpoints",
                    0,
                    "appendContinuationItemsAction",
                    "continuationItems",
                )

            if not items:
                if yt_info:
                    continuation = (
                        _extract_browse_continuation_token_from_response(
                            yt_info,
                        )
                    )
                    if continuation:
                        continue
                break

            continuation = None
            for item in items:
                vid = item.get("playlistVideoRenderer")
                continuation_item = item.get("continuationItemRenderer")

                if vid:
                    yield _parse_video(vid)
                elif continuation_item:
                    continuation = multi_get(
                        continuation_item,
                        "continuationEndpoint",
                        "continuationCommand",
                        "token",
                    )

            if not continuation:
                break

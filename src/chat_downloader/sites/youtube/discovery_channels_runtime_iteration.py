# SPDX-License-Identifier: MIT

"""Channel discovery iteration logic for YouTube channel/user listings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.errors import InvalidParameter, NoVideos, UserNotFound
from chat_downloader.utils.dict_utils import multi_get

from .client_context import _get_innertube_context
from .client_requests_continuation import _get_continuation_info
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _VIDEO_TYPE_REMAPPING,
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .helpers import _extract_browse_continuation_token_from_response
from .parsing.messages import _parse_video

if TYPE_CHECKING:
    from collections.abc import Iterable

    from chat_downloader.models import ChatRequest


def _build_channel_url(
    channel_id: str | None,
    user_id: str | None,
    custom_username: str | None,
    handle: str | None,
    vid_type: str,
) -> tuple[str, str]:
    """Return (user_url, browse_url) for the given channel identifier."""
    if channel_id:
        _type, _id = "channel/", channel_id
    elif user_id:
        _type, _id = "user/", user_id
    elif custom_username:
        _type, _id = "c/", custom_username
    elif handle:
        _type, _id = "@", handle
    else:
        msg = "No user type specified."
        raise InvalidParameter(msg)
    user_url = f"{_YT_HOME}/{_type}{_id}"
    return user_url, f"{user_url}/{vid_type}"


def _select_videos_tab(
    yt_info: dict[str, Any], user_url: str, video_type: str
) -> Any:
    """Return the page_contents for the requested tab, or raise."""
    tabs = multi_get(
        yt_info, "contents", "twoColumnBrowseResultsRenderer", "tabs"
    )
    if not tabs:
        msg = f'Unable to find user: "{user_url}"'
        raise UserNotFound(msg)

    for tab in tabs:
        tab_data = tab.get("tabRenderer", {})
        if not tab_data or not tab_data.get("selected"):
            continue
        tab_title = tab_data.get("title", "").lower()
        if tab_title != video_type.lower():
            log(
                "debug",
                f'"{tab_title}" tab is not visible for this channel '
                "(i.e. there are no such videos).",
            )
            msg = (
                "This channel has no videos of the requested type "
                f"({video_type})."
            )
            raise NoVideos(msg)
        return tab_data.get("content")
    return None


def _process_page_items(
    items: list[Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return video dicts and next continuation token from a browse page."""
    videos: list[dict[str, Any]] = []
    token: str | None = None
    for item in items:
        rich = multi_get(item, "richItemRenderer", "content") or {}
        vid = rich.get("videoRenderer")
        lockup = rich.get("lockupViewModel")
        cont_item = item.get("continuationItemRenderer")
        if vid:
            videos.append(_parse_video(vid))
        elif lockup:
            videos.append(_parse_video({"lockupViewModel": lockup}))
        elif cont_item:
            token = multi_get(
                cont_item,
                "continuationEndpoint",
                "continuationCommand",
                "token",
            )
    return videos, token


def get_user_videos(
    self: Any,
    channel_id: str | None = None,
    user_id: str | None = None,
    custom_username: str | None = None,
    handle: str | None = None,
    video_type: str = "videos",
    params: ChatRequest | None = None,
) -> Iterable[dict[str, Any]]:
    """Yield videos for a channel/user discovery request."""
    from chat_downloader.models import ChatRequest

    request = ChatRequest() if params is None else params

    vid_type = _VIDEO_TYPE_REMAPPING.get(video_type.lower())
    if not vid_type:
        msg = (
            "Invalid argument passed for video_type. "
            f"Must be one of {set(_VIDEO_TYPE_REMAPPING.keys())}"
        )
        raise InvalidParameter(msg)

    user_url, browse_url = _build_channel_url(
        channel_id, user_id, custom_username, handle, vid_type
    )
    yt_info, ytcfg, _ = _get_initial_info(
        browse_url,
        self._session_get,
        request,
        _YT_INITIAL_DATA_RE,
        _YT_CFG_RE,
        _YT_INITIAL_PLAYER_RESPONSE_RE,
    )

    page_contents = _select_videos_tab(yt_info, user_url, video_type)

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
                page_contents or {}, "richGridRenderer", "contents"
            )
            first_time = False
        else:
            if continuation in seen_continuations:
                log(
                    "debug",
                    "Detected YouTube browse continuation loop; assuming "
                    "end of feed.",
                )
                break
            if continuation:
                seen_continuations.add(continuation)
            continuation_params["continuation"] = continuation
            yt_info = _get_continuation_info(
                continuation_url,
                self._session_post,
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
                continuation = _extract_browse_continuation_token_from_response(
                    yt_info
                )
                if continuation:
                    continue
            break

        videos, continuation = _process_page_items(items)
        yield from videos

        if not continuation:
            break

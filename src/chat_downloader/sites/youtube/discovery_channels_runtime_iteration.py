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
from .helpers import (
    _extract_browse_continuation_token_from_response,
    require_innertube_api_key,
)
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


def _fetch_browse_continuation(
    self: Any,
    continuation: str | None,
    continuation_url: str,
    continuation_params: dict[str, Any],
    request: ChatRequest,
    seen_continuations: set[str],
) -> tuple[list[Any] | None, dict[str, Any] | None]:
    """Fetch the next page via browse continuation.

    Returns ``(items, yt_info)``.  Returns ``(None, None)`` when the caller
    should stop (loop detected or no continuation token).
    """
    if continuation in seen_continuations:
        log(
            "debug",
            "Detected YouTube browse continuation loop; assuming end of feed.",
        )
        return None, None
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
    return items, yt_info


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

    api_key = require_innertube_api_key(ytcfg)
    continuation_url = f"{_YT_HOME}/youtubei/v1/browse?key={api_key}"
    continuation_params: dict[str, Any] = {
        "context": _get_innertube_context(ytcfg)
    }

    # Process the first page directly; subsequent pages come from continuations.
    first_items: list[Any] = (
        multi_get(page_contents or {}, "richGridRenderer", "contents") or []
    )
    videos, continuation = _process_page_items(first_items)
    yield from videos

    seen_continuations: set[str] = set()
    while continuation:
        items, yt_info = _fetch_browse_continuation(
            self,
            continuation,
            continuation_url,
            continuation_params,
            request,
            seen_continuations,
        )
        if items is None and yt_info is None:
            break
        if not items:
            continuation = (
                _extract_browse_continuation_token_from_response(yt_info)
                if yt_info
                else None
            )
            continue
        videos, continuation = _process_page_items(items)
        yield from videos

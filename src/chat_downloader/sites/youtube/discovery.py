# SPDX-License-Identifier: MIT

"""Channel discovery and fixture URL generation for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import InvalidParameter, NoVideos, UserNotFound
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import dig, get_dict

from .client_context import _get_innertube_context
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _LIVE_PLAYLIST_URL,
    _VIDEO_TYPE_REMAPPING,
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
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
    from chat_downloader.utils.json_types import JSONAny, JSONDict, JSONList

    from ._protocols import YouTubeDownloaderProto


def _iter_playlist_urls(content: JSONAny) -> Iterator[str]:
    """Yield playlist URLs from a rendered discovery content tree."""
    if isinstance(content, dict):
        shelf = get_dict(content, "shelfRenderer")
        url = dig(shelf, "endpoint", "commandMetadata", "webCommandMetadata", "url")
        if isinstance(url, str) and url.startswith("/playlist?"):
            yield _YT_HOME + url

        for value in content.values():
            yield from _iter_playlist_urls(value)
        return

    if isinstance(content, list):
        for item in content:
            yield from _iter_playlist_urls(item)


def _iter_video_ids(content: JSONAny) -> Iterator[str]:
    """Yield video IDs from a rendered discovery content tree."""
    if isinstance(content, dict):
        video = get_dict(content, "videoRenderer")
        video_id = video.get("videoId")
        if isinstance(video_id, str) and video_id:
            yield video_id

        for value in content.values():
            yield from _iter_video_ids(value)
        return

    if isinstance(content, list):
        for item in content:
            yield from _iter_video_ids(item)


def _get_rendered_content(yt_info: JSONDict, tab_index: int = 0) -> JSONAny:
    """Extract rendered playlist content from YouTube initial data."""
    return cast(
        "JSONAny",
        multi_get(
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
        ),
    )


def _build_channel_url(
    channel_id: str | None,
    user_id: str | None,
    custom_username: str | None,
    handle: str | None,
    video_path: str,
) -> tuple[str, str]:
    """Return the channel root and requested discovery-tab URLs."""
    if channel_id:
        route, identifier = "channel/", channel_id
    elif user_id:
        route, identifier = "user/", user_id
    elif custom_username:
        route, identifier = "c/", custom_username
    elif handle:
        identifier = handle.lstrip("@")
        if not identifier:
            msg = "Invalid YouTube handle."
            raise InvalidParameter(msg)
        route = "@"
    else:
        msg = "No user type specified."
        raise InvalidParameter(msg)
    user_url = f"{_YT_HOME}/{route}{identifier}"
    return user_url, f"{user_url}/{video_path}"


def _select_videos_tab(
    yt_info: JSONDict,
    user_url: str,
    video_type: str,
) -> JSONAny:
    """Return the requested selected tab's content, or raise."""
    tabs = multi_get(yt_info, "contents", "twoColumnBrowseResultsRenderer", "tabs")
    if not isinstance(tabs, list) or not tabs:
        msg = f'Unable to find user: "{user_url}"'
        raise UserNotFound(msg)

    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_data = get_dict(tab, "tabRenderer")
        if not tab_data.get("selected"):
            continue
        raw_title = tab_data.get("title")
        tab_title = raw_title.lower() if isinstance(raw_title, str) else ""
        if tab_title != video_type.lower():
            log(
                "debug",
                f'"{tab_title}" tab is not visible for this channel '
                "(i.e. there are no such videos).",
            )
            msg = f"This channel has no videos of the requested type ({video_type})."
            raise NoVideos(msg)
        return tab_data.get("content")
    return None


def _process_page_items(
    items: JSONList,
) -> tuple[list[JSONDict], str | None]:
    """Return parsed videos and the next token from a browse page."""
    videos: list[JSONDict] = []
    token: str | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        rich = get_dict(get_dict(item, "richItemRenderer"), "content")
        video = get_dict(rich, "videoRenderer")
        lockup = get_dict(rich, "lockupViewModel")
        continuation = _extract_browse_continuation_token_from_item(item)
        if video:
            videos.append(_parse_video(video))
        elif lockup:
            videos.append(_parse_video({"lockupViewModel": lockup}))
        elif continuation:
            token = continuation
    return videos, token


class YouTubeDiscoveryMixin:
    """Enumerate channel videos and generate provider test URLs."""

    def generate_urls(self, **kwargs: Any) -> Iterator[str]:  # noqa: ARG002 — base class contract
        """Generate URLs for provider integration tests."""
        for item in self._get_testing_items():
            yield f"{_YT_HOME}/watch?v={item['video_id']}"

    def _get_testing_items(self) -> Iterator[dict[str, Any]]:
        """Get test items from YouTube's live discovery channel."""
        params = {"max_attempts": 10}
        proto = cast("YouTubeDownloaderProto", self)
        yt_initial_data, _, _ = _get_initial_info(
            _LIVE_PLAYLIST_URL,
            proto._session_get,
            params,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )
        two_column = multi_get(
            yt_initial_data,
            "contents",
            "twoColumnBrowseResultsRenderer",
        )
        tabs = multi_get(two_column or {}, "tabs") or []
        first_tab = tabs[0] if isinstance(tabs, list) and tabs else {}
        tab_content = multi_get(first_tab, "tabRenderer", "content") or {}

        yielded_video_ids: set[str] = set()
        for video_id in _iter_video_ids(tab_content):
            if video_id in yielded_video_ids:
                continue
            yielded_video_ids.add(video_id)
            yield {"video_id": video_id}

        for playlist_url in _iter_playlist_urls(tab_content):
            yield from proto.get_playlist_items(playlist_url)

    def get_user_videos(
        self,
        channel_id: str | None = None,
        user_id: str | None = None,
        custom_username: str | None = None,
        handle: str | None = None,
        video_type: str = "videos",
        params: ChatRequest | dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield all videos listed on a user's requested discovery tab."""
        from chat_downloader.models import ChatRequest as ChatRequestModel

        if not isinstance(video_type, str) or not video_type:
            msg = "Invalid argument passed for video_type. Must be a non-empty string."
            raise InvalidParameter(msg)

        proto = cast("YouTubeDownloaderProto", self)
        request = (
            ChatRequestModel() if params is None else proto._coerce_chat_request(params)
        )
        video_path = _VIDEO_TYPE_REMAPPING.get(video_type.lower())
        if not video_path:
            allowed = set(_VIDEO_TYPE_REMAPPING)
            msg = f"Invalid argument passed for video_type. Must be one of {allowed}"
            raise InvalidParameter(msg)

        user_url, browse_url = _build_channel_url(
            channel_id,
            user_id,
            custom_username,
            handle,
            video_path,
        )
        yt_info, ytcfg, _ = _get_initial_info(
            browse_url,
            proto._session_get,
            request,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )
        page_contents = _select_videos_tab(yt_info, user_url, video_type)
        api_key = require_innertube_api_key(ytcfg)
        continuation_url = f"{_YT_HOME}/youtubei/v1/browse?key={api_key}"
        continuation_params: JSONDict = {
            "context": _get_innertube_context(ytcfg),
        }

        page_container = page_contents if isinstance(page_contents, dict) else {}
        first_items_raw = multi_get(
            page_container,
            "richGridRenderer",
            "contents",
        )
        first_items: JSONList = (
            first_items_raw if isinstance(first_items_raw, list) else []
        )
        videos, continuation = _process_page_items(first_items)
        yield from videos

        seen_continuations: set[str] = set()
        while continuation:
            items, continuation_info = _fetch_browse_continuation(
                proto,
                continuation,
                continuation_url,
                continuation_params,
                ytcfg,
                request,
                seen_continuations,
            )
            if items is None and continuation_info is None:
                break
            if not items:
                continuation = (
                    _extract_browse_continuation_token_from_response(
                        continuation_info,
                    )
                    if continuation_info
                    else None
                )
                continue
            videos, continuation = _process_page_items(items)
            yield from videos

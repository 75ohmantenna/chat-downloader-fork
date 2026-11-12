# SPDX-License-Identifier: MIT

"""Twitch GraphQL-backed discovery helpers."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError, UserNotFound
from chat_downloader.sites.remap import (
    Remapper as r,  # noqa: N813 — compact table-construction alias; used as r("key", ...) throughout remapping tables
)
from chat_downloader.sites.twitch.remappings import (
    build_clip_remapping,
    build_livestream_remapping,
    build_video_remapping,
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_list, get_str

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.utils.json_types import JSONAny, JSONDict, JSONList

    from ._protocols import _DownloadGQL, _SessionPost


def _iter_discovery_edges(
    fetch_page: Callable[[str | None, int], JSONDict | None],
    *,
    limit: int | None,
    page_size: int,
    initial_cursor: str | None,
    kind: str,
) -> Generator[JSONAny, None, None]:
    """Own edge quota and validate required cursor progress before page emission."""
    remaining: float = float("inf") if limit is None else limit
    cursor = initial_cursor
    seen_cursors = {cursor} if cursor is not None else set()
    while True:
        num_to_get = int(max(min(remaining, page_size), 0))
        if num_to_get <= 0:
            return
        page = fetch_page(cursor, num_to_get)
        if not page:
            return
        edges = get_list(page, "edges")
        has_next_page = bool(multi_get(page, "pageInfo", "hasNextPage"))
        if has_next_page and len(edges) < remaining:
            final_edge = edges[-1] if edges and isinstance(edges[-1], dict) else {}
            next_cursor = get_str(final_edge, "cursor")
            if not next_cursor or next_cursor in seen_cursors:
                msg = f"Twitch {kind} discovery did not advance its pagination cursor."
                raise ParsingError(msg)
            cursor = next_cursor
            seen_cursors.add(cursor)
        remaining -= len(edges)
        yield from edges
        if not has_next_page:
            return


def get_user_clips(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    username: str,
    limit: int = 100,
    filter_by: str = "LAST_WEEK",
) -> Generator[dict[str, Any], None, None]:
    """Get clips for a user."""
    clip_remapping = build_clip_remapping()

    def fetch_page(cursor: str | None, num_to_get: int) -> JSONDict | None:
        query: JSONList = [
            {
                "operationName": "ClipsCards__User",
                "variables": {
                    "cursor": cursor,
                    "login": username,
                    "limit": num_to_get,
                    "criteria": {"filter": filter_by},
                },
            },
        ]
        info = download_gql_func(session_post, query)
        if not info:
            return None
        clips: JSONDict | None = multi_get(info, 0, "data", "user", "clips")
        return clips

    for edge in _iter_discovery_edges(
        fetch_page,
        limit=limit,
        page_size=100,
        initial_cursor=base64.b64encode(b"0").decode(),
        kind="clip",
    ):
        if not isinstance(edge, dict):
            continue
        node = get_dict(edge, "node")
        yield r.remap_dict(node, clip_remapping)


def _build_user_videos_query(
    username: str,
    num_to_get: int,
    video_type: str | None,
    sort: str,
    cursor: str | None,
) -> JSONList:
    """Build the FilterableVideoTower_Videos GQL query payload."""
    variables: JSONDict = {
        "limit": num_to_get,
        "channelOwnerLogin": username,
        "broadcastType": video_type,
        "videoSort": sort,
    }
    if cursor is not None:
        variables["cursor"] = cursor
    query_item: JSONDict = {
        "operationName": "FilterableVideoTower_Videos",
        "variables": variables,
    }
    return cast("JSONList", [query_item])


def _extract_user_videos(
    info: JSONList,
    username: str,
) -> JSONDict | None:
    """Validate user existence and return the videos dict, or None to break."""
    if multi_get(info, 0, "data", "user", "id") == "":
        msg = f'Channel "{username}" not found'
        raise UserNotFound(msg)
    videos: JSONDict | None = multi_get(info, 0, "data", "user", "videos")
    return videos


def get_user_videos(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    username: str,
    limit: int | None = None,
    video_type: str | None = None,
    sort: str = "TIME",
) -> Generator[dict[str, Any], None, None]:
    """Get videos for a user."""
    video_remapping = build_video_remapping()

    def fetch_page(cursor: str | None, num_to_get: int) -> JSONDict | None:
        query = _build_user_videos_query(username, num_to_get, video_type, sort, cursor)
        info = download_gql_func(session_post, query)
        return _extract_user_videos(info, username)

    for edge in _iter_discovery_edges(
        fetch_page,
        limit=limit,
        page_size=30,
        initial_cursor=None,
        kind="video",
    ):
        if not isinstance(edge, dict):
            continue
        node = get_dict(edge, "node")
        if not node:
            continue
        yield r.remap_dict(node, video_remapping)


def get_top_livestreams(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    limit: int = 30,
) -> Generator[dict[str, Any], None, None]:
    """Get top live streams on Twitch."""
    livestream_remapping = build_livestream_remapping()

    def fetch_page(cursor: str | None, num_to_get: int) -> JSONDict | None:
        query: JSONList = [
            {
                "operationName": "BrowsePage_Popular",
                "variables": {
                    "limit": num_to_get,
                    "cursor": cursor,
                    "includeCostreaming": False,
                    "platformType": "all",
                    "options": {"sort": "VIEWER_COUNT"},
                    "sortTypeIsRecency": False,
                },
            },
        ]

        info = download_gql_func(session_post, query)
        streams_info: JSONDict | None = multi_get(info, 0, "data", "streams")
        if not streams_info:
            log(
                "warning",
                "Could not retrieve Twitch livestream data from GraphQL response.",
            )
        return streams_info

    for edge in _iter_discovery_edges(
        fetch_page,
        limit=limit,
        page_size=30,
        initial_cursor="",
        kind="livestream",
    ):
        if not isinstance(edge, dict):
            continue
        node = get_dict(edge, "node")
        yield r.remap_dict(node, livestream_remapping)

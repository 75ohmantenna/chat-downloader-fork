# SPDX-License-Identifier: MIT

"""Twitch GraphQL-backed discovery helpers."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
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
    from collections.abc import Generator

    from chat_downloader.utils.json_types import JSONDict, JSONList

    from ._protocols import _DownloadGQL, _SessionPost


def get_user_clips(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    username: str,
    limit: int = 100,
    filter_by: str = "LAST_WEEK",
) -> Generator[dict[str, Any], None, None]:
    """Get clips for a user."""
    clip_remapping = build_clip_remapping()

    remaining_count = limit
    offset = 0
    while True:
        num_to_get = max(min(remaining_count, 100), 0)
        if num_to_get <= 0:
            break

        query: JSONList = [
            {
                "operationName": "ClipsCards__User",
                "variables": {
                    "cursor": base64.b64encode(str(offset).encode()).decode(),
                    "login": username,
                    "limit": num_to_get,
                    "criteria": {"filter": filter_by},
                },
            },
        ]
        info = download_gql_func(session_post, query)
        if not info:
            break

        clips = multi_get(info, 0, "data", "user", "clips")
        if not clips:
            break
        edges = clips.get("edges") or []
        remaining_count -= len(edges)

        for edge in edges:
            node = edge.get("node") or {}
            yield r.remap_dict(node, clip_remapping)

        if not multi_get(clips, "pageInfo", "hasNextPage"):
            break


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
        from chat_downloader.errors import UserNotFound

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

    remaining_count: float = float("inf") if limit is None else limit
    cursor: str | None = None

    while True:
        num_to_get = int(max(min(remaining_count, 30), 0))
        if num_to_get <= 0:
            break

        query = _build_user_videos_query(username, num_to_get, video_type, sort, cursor)
        info = download_gql_func(session_post, query)
        if not info:
            break

        videos = _extract_user_videos(info, username)
        if not videos:
            break

        edges = get_list(videos, "edges")
        remaining_count -= len(edges)

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            cursor = get_str(edge, "cursor") or None
            node = get_dict(edge, "node")
            if not node:
                continue
            yield r.remap_dict(node, video_remapping)

        if not multi_get(videos, "pageInfo", "hasNextPage"):
            break


def get_top_livestreams(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    limit: int = 30,
) -> Generator[dict[str, Any], None, None]:
    """Get top live streams on Twitch."""
    livestream_remapping = build_livestream_remapping()

    remaining_count = limit
    cursor = ""

    while True:
        num_to_get = max(min(remaining_count, 30), 0)
        if num_to_get <= 0:
            break

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
        streams_info = multi_get(info, 0, "data", "streams")
        if not streams_info:
            log(
                "warning",
                "Could not retrieve Twitch livestream data from GraphQL response.",
            )
            break

        edges = streams_info.get("edges") or []
        if not edges:
            break

        cursor = edges[-1].get("cursor") or ""
        remaining_count -= num_to_get

        for edge in edges:
            node = edge.get("node") or {}
            yield r.remap_dict(node, livestream_remapping)

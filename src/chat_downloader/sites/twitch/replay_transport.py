# SPDX-License-Identifier: MIT

"""Low-level Twitch replay transport helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat_downloader.utils.json_types import JSONDict, get_dict

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONList


def get_chat_messages_by_vod_id(
    session_post: object,  # noqa: ARG001 — uniform transport callable signature; live transport uses this
    download_gql_func: Callable[[JSONList], list[JSONDict]],
    vod_id: str,
    cursor: str | None,
    content_offset_seconds: float | None,
) -> tuple[JSONDict | None, JSONDict | None]:
    """Get chat replay messages for a VOD by ID."""
    variables: JSONDict = {
        "videoID": vod_id,
    }

    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = content_offset_seconds or 0

    query: JSONList = [
        {
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variables,
        }
    ]
    result = download_gql_func(query)

    data = get_dict(result[0], "data") if result else {}
    info = get_dict(data, "video")
    if not info:
        return None, None

    comments = info.get("comments")
    if not isinstance(comments, dict):
        return None, None

    return comments, info

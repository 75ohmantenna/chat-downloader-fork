# SPDX-License-Identifier: MIT

"""Low-level Twitch replay transport helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat_downloader.errors import ParsingError
from chat_downloader.utils.json_types import (
    JSONAny,
    JSONDict,
    get_dict,
    get_list,
    get_str,
)
from chat_downloader.utils.timed_generator import _check_polling_cancelled

from .graphql_client import _PersistedQueryUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONList


def _validated_comments(result: JSONAny) -> tuple[JSONDict, JSONDict]:
    """Distinguish an explicit empty page from missing replay data on every page."""
    data = (
        get_dict(result[0], "data")
        if isinstance(result, list) and result and isinstance(result[0], dict)
        else {}
    )
    info = get_dict(data, "video")
    if not info:
        msg = "Twitch replay video disappeared from the response"
        raise ParsingError(msg)
    comments = info.get("comments")
    if not isinstance(comments, dict) or not isinstance(comments.get("edges"), list):
        msg = "Twitch replay comments disappeared or were malformed"
        raise ParsingError(msg)
    return comments, info


def get_chat_messages_by_vod_id(
    session_post: object,  # noqa: ARG001 — uniform transport callable signature; live transport uses this
    download_gql_func: Callable[[JSONList], JSONList],
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
    try:
        _check_polling_cancelled()
        result = download_gql_func(query)
    except _PersistedQueryUnavailable:
        fallback_variables: JSONDict = {"vodId": vod_id}
        if cursor:
            fallback_variables["after"] = cursor
        else:
            fallback_variables["contentOffsetSeconds"] = int(
                content_offset_seconds or 0
            )
        _check_polling_cancelled()
        result = download_gql_func(
            [
                {
                    "operationName": "VideoCommentsQuery",
                    "variables": fallback_variables,
                }
            ]
        )
        _check_polling_cancelled()
        fallback_comments, info = _validated_comments(result)
        edges = get_list(fallback_comments, "edges")
        final_edge = edges[-1] if edges and isinstance(edges[-1], dict) else {}
        final_cursor = get_str(final_edge, "cursor")
        normalized_comments: JSONDict = {
            **fallback_comments,
            "pageInfo": {"hasNextPage": bool(final_cursor)},
        }
        return normalized_comments, info

    _check_polling_cancelled()
    return _validated_comments(result)

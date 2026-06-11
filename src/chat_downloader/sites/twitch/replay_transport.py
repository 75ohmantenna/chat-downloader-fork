# SPDX-License-Identifier: MIT

"""Low-level Twitch replay transport helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def get_chat_messages_by_vod_id(
    session_post: Callable[..., Any],  # noqa: ARG001 — uniform transport callable signature; live transport uses this
    download_gql_func: Callable[..., Any],
    vod_id: str,
    cursor: str | None,
    content_offset_seconds: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Get chat replay messages for a VOD by ID."""
    variables: dict[str, Any] = {
        "videoID": vod_id,
    }

    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = content_offset_seconds or 0

    query = [
        {
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variables,
        }
    ]
    result = download_gql_func(query)

    try:
        info = result[0]["data"]["video"]
    except (KeyError, IndexError, TypeError):
        return None, None

    if not info:
        return None, None

    comments = info.get("comments")
    if not comments:
        return None, None

    return comments, info

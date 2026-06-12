# SPDX-License-Identifier: MIT

"""Fallback YouTube bootstrap requests backed by InnerTube endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

from chat_downloader.request_profiles import (
    REQUEST_PROFILE_INNERTUBE_CONTEXTS,
)
from chat_downloader.utils.dict_utils import multi_get

from .client_context import apply_request_profile_to_innertube_context
from .constants_patterns import _YT_HOME
from .helpers import extract_chat_submenu_continuations

# YouTube's public web InnerTube key — used only when no ytcfg is available yet.
_DEFAULT_INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_DEFAULT_FALLBACK_PROFILE = "youtube_web"


def _fallback_profile(profile_name: object) -> str:
    return profile_name if isinstance(profile_name, str) else _DEFAULT_FALLBACK_PROFILE


def _fallback_context(profile_name: object) -> JSONDict:
    profile = _fallback_profile(profile_name)
    base_context = REQUEST_PROFILE_INNERTUBE_CONTEXTS.get(
        _DEFAULT_FALLBACK_PROFILE,
        {},
    )
    return apply_request_profile_to_innertube_context(base_context, profile)


def _post_innertube_json(
    session_post: Any,
    endpoint: str,
    payload: JSONDict,
) -> JSONDict:
    response = session_post(
        f"{_YT_HOME}/youtubei/v1/{endpoint}?key={_DEFAULT_INNERTUBE_API_KEY}",
        json=payload,
    )
    data = response.json()
    return data if isinstance(data, dict) else {}


def _extract_primary_live_continuation(
    yt_next_data: JSONDict,
) -> str | None:
    continuation = multi_get(
        yt_next_data,
        "contents",
        "twoColumnWatchNextResults",
        "conversationBar",
        "liveChatRenderer",
        "continuations",
        0,
        "reloadContinuationData",
        "continuation",
    )
    return continuation if isinstance(continuation, str) else None


def _build_fallback_ytcfg(
    context: JSONDict,
    player_response: JSONDict,
    next_response: JSONDict,
) -> JSONDict:
    client = context.get("client") if isinstance(context, dict) else {}
    if not isinstance(client, dict):
        client = {}

    response_visitor_data = multi_get(
        next_response, "responseContext", "visitorData"
    ) or multi_get(player_response, "responseContext", "visitorData")
    if response_visitor_data:
        client = {**client, "visitorData": response_visitor_data}
        context = {**context, "client": client}

    return {
        "INNERTUBE_API_KEY": _DEFAULT_INNERTUBE_API_KEY,
        "INNERTUBE_CONTEXT": context,
        "INNERTUBE_CONTEXT_CLIENT_NAME": client.get("clientName"),
        "INNERTUBE_CLIENT_VERSION": client.get("clientVersion"),
    }


def _build_fallback_initial_data(
    yt_next_data: JSONDict,
) -> JSONDict:
    initial_data = dict(yt_next_data)
    continuation_info = extract_chat_submenu_continuations(initial_data)
    primary_continuation = _extract_primary_live_continuation(initial_data)
    if primary_continuation:
        # The selector submenu continuations from youtubei/v1/next can be
        # too short for the first live_chat poll. The primary renderer
        # continuation is the one observed to bootstrap the polling loop.
        continuation_info["Live chat"] = primary_continuation
        continuation_info.setdefault("Top chat", primary_continuation)
    if continuation_info:
        initial_data["_chat_downloader_continuation_info"] = cast(
            "JSONDict", continuation_info
        )
    return initial_data


def get_innertube_video_bootstrap(
    video_id: str,
    session_post: Any,
    request_profile: object,
) -> tuple[JSONDict, JSONDict, JSONDict]:
    """Return initial data, config, and player response from InnerTube."""
    context = _fallback_context(request_profile)
    payload: JSONDict = {"context": context, "videoId": video_id}

    player_response = _post_innertube_json(session_post, "player", payload)
    next_response = _post_innertube_json(session_post, "next", payload)

    yt_initial_data = _build_fallback_initial_data(next_response)
    ytcfg = _build_fallback_ytcfg(context, player_response, next_response)
    return yt_initial_data, ytcfg, player_response


__all__ = ["get_innertube_video_bootstrap"]

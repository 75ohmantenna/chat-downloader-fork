# SPDX-License-Identifier: MIT

"""Helper utilities shared by YouTube extractor modules.

This module centralizes helper functions that are currently reused across
multiple YouTube modules after the large extractor refactor.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_str

from .client_auth import _generate_sapisidhash_header
from .client_context import _generate_headers
from .client_requests_continuation import _get_continuation_info
from .constants_patterns import _YT_HOME

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict, JSONList

    from ._protocols import YouTubeDownloaderProto


def require_innertube_api_key(ytcfg: JSONDict) -> str:
    """Return the INNERTUBE_API_KEY from ytcfg or raise ParsingError."""
    api_key = ytcfg.get("INNERTUBE_API_KEY")
    if not isinstance(api_key, str) or not api_key:
        msg = (
            "YouTube INNERTUBE_API_KEY missing from ytcfg; "
            "cannot build an InnerTube request URL."
        )
        raise ParsingError(msg)
    return api_key


def _extract_browse_continuation_token_from_item(item: object) -> str | None:
    """Extract a token from legacy or view-model browse continuation items."""
    if not isinstance(item, dict):
        return None
    token_candidates = (
        multi_get(
            item,
            "continuationItemRenderer",
            "continuationEndpoint",
            "continuationCommand",
            "token",
        ),
        multi_get(
            item,
            "continuationItemViewModel",
            "continuationCommand",
            "innertubeCommand",
            "continuationCommand",
            "token",
        ),
    )
    for token in token_candidates:
        if isinstance(token, str) and token:
            return token
    return None


def _extract_browse_continuation_token(items: object) -> str | None:
    """Extract continuation token from a list of browse continuation items."""
    if not isinstance(items, list):
        return None

    for item in items:
        if token := _extract_browse_continuation_token_from_item(item):
            return token
    return None


def _browse_api_headers(
    downloader: YouTubeDownloaderProto,
    ytcfg: JSONDict,
    continuation_params: JSONDict,
) -> dict[str, str]:
    """Generate API headers from the same context used in the request body."""
    request_ytcfg = copy.deepcopy(ytcfg)
    context = get_dict(continuation_params, "context")
    if context:
        request_ytcfg["INNERTUBE_CONTEXT"] = copy.deepcopy(context)
    return _generate_headers(
        request_ytcfg,
        downloader,
        _YT_HOME,
        _generate_sapisidhash_header,
    )


def _update_browse_visitor_data(
    continuation_params: JSONDict,
    yt_info: JSONDict,
) -> None:
    """Roll response visitor data into the next browse request context."""
    visitor_data = get_str(get_dict(yt_info, "responseContext"), "visitorData")
    if not visitor_data:
        return
    context = get_dict(continuation_params, "context")
    client = get_dict(context, "client")
    client["visitorData"] = visitor_data
    context["client"] = client
    continuation_params["context"] = context


def _extract_menu_continuation_token(item: object) -> str | None:
    """Extract a continuation token from a chat menu item.

    YouTube has used several shapes here over time, including direct
    ``reloadContinuationData`` and endpoint-style ``continuationCommand`` forms.
    """
    if not isinstance(item, dict):
        return None

    token_candidates: tuple[str | None, ...] = (
        multi_get(item, "continuation", "reloadContinuationData", "continuation"),
        multi_get(
            item,
            "continuation",
            "continuationEndpoint",
            "continuationCommand",
            "token",
        ),
        multi_get(
            item,
            "continuation",
            "continuationEndpoint",
            "getLiveChatEndpoint",
            "continuation",
        ),
        multi_get(item, "continuationEndpoint", "continuationCommand", "token"),
        multi_get(item, "continuationEndpoint", "getLiveChatEndpoint", "continuation"),
    )
    for token in token_candidates:
        if token:
            return token
    return None


def extract_chat_submenu_continuations(
    yt_data: JSONDict,
    fallback_labels: list[str] | None = None,
) -> dict[str, str]:
    """Extract chat-menu continuation tokens from a YouTube live-chat payload.

    Preferred behavior is to use the menu item ``title`` as the mapping key. If
    titles are absent but callers know the expected label order,
    ``fallback_labels`` can be supplied and will be assigned positionally.
    """
    sub_menu_items = (
        multi_get(
            yt_data,
            "contents",
            "twoColumnWatchNextResults",
            "conversationBar",
            "liveChatRenderer",
            "header",
            "liveChatHeaderRenderer",
            "viewSelector",
            "sortFilterSubMenuRenderer",
            "subMenuItems",
        )
        or multi_get(
            yt_data,
            "continuationContents",
            "liveChatContinuation",
            "header",
            "liveChatHeaderRenderer",
            "viewSelector",
            "sortFilterSubMenuRenderer",
            "subMenuItems",
        )
        or []
    )

    if not isinstance(sub_menu_items, list):
        return {}

    labeled: dict[str, str] = {}
    unlabeled_tokens: list[str] = []

    for item in sub_menu_items:
        if not isinstance(item, dict):
            continue
        token = _extract_menu_continuation_token(item)
        if not token:
            continue
        title = item.get("title")
        if isinstance(title, str) and title:
            labeled[title] = token
        else:
            unlabeled_tokens.append(token)

    if fallback_labels:
        for label, token in zip(fallback_labels, unlabeled_tokens, strict=False):
            labeled.setdefault(label, token)

    return labeled


def _fetch_browse_continuation(
    self: YouTubeDownloaderProto,
    continuation: str | None,
    continuation_url: str,
    continuation_params: JSONDict,
    ytcfg: JSONDict,
    request: ChatRequest,
    seen_continuations: set[str],
) -> tuple[JSONList | None, JSONDict | None]:
    """Fetch the next page via browse continuation.

    Returns ``(items, yt_info)``.  Returns ``(None, None)`` when the caller
    should stop (loop detected or no continuation token).
    """
    if continuation in seen_continuations:
        log(
            "debug",
            "Detected YouTube browse continuation loop; assuming end of results.",
        )
        return None, None
    if continuation:
        seen_continuations.add(continuation)
    continuation_params["continuation"] = continuation
    headers = _browse_api_headers(self, ytcfg, continuation_params)
    yt_info = _get_continuation_info(
        continuation_url,
        self._session_post,
        request,
        require_live_chat_continuation=False,
        headers=headers,
        json=continuation_params,
    )
    _update_browse_visitor_data(continuation_params, yt_info)
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


def _extract_browse_continuation_token_from_response(
    yt_info: JSONDict,
) -> str | None:
    """Extract a continuation token even when page has no video items.

    YouTube sometimes returns a continuation-only response for browse/playlist
    pages. If we stop on ``items == []`` directly, pagination can truncate
    early.
    """
    continuation_items_candidates = (
        multi_get(
            yt_info,
            "onResponseReceivedActions",
            0,
            "appendContinuationItemsAction",
            "continuationItems",
        ),
        multi_get(
            yt_info,
            "onResponseReceivedActions",
            0,
            "reloadContinuationItemsCommand",
            "continuationItems",
        ),
        multi_get(
            yt_info,
            "onResponseReceivedEndpoints",
            0,
            "appendContinuationItemsAction",
            "continuationItems",
        ),
        multi_get(
            yt_info,
            "onResponseReceivedEndpoints",
            0,
            "reloadContinuationItemsCommand",
            "continuationItems",
        ),
        multi_get(
            yt_info,
            "continuationContents",
            "playlistVideoListContinuation",
            "contents",
        ),
        multi_get(yt_info, "continuationContents", "gridContinuation", "items"),
    )

    for candidate in continuation_items_candidates:
        token = _extract_browse_continuation_token(candidate)
        if token:
            return token
    return None

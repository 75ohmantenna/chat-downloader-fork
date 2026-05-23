# SPDX-License-Identifier: MIT

"""Helper utilities shared by YouTube extractor modules.

This module centralizes helper functions that are currently reused across
multiple YouTube modules after the large extractor refactor.
"""

from typing import Any

from chat_downloader.utils.dict_utils import multi_get


def _safe_get_dict(obj: dict[str, Any], key: str) -> dict[str, Any]:
    """Get dict value with empty dict default if missing or falsy.

    :param obj: Dictionary to get value from
    :param key: Key to look up
    :return: Value at key or empty dict if missing/falsy
    """
    return obj.get(key) or {}


def _extract_browse_continuation_token(items: Any) -> str | None:
    """Extract continuation token from a list of browse continuation items."""
    if not isinstance(items, list):
        return None

    for item in items:
        token = multi_get(
            item,
            "continuationItemRenderer",
            "continuationEndpoint",
            "continuationCommand",
            "token",
        )
        if token:
            return token
    return None


def _extract_menu_continuation_token(item: object) -> str | None:
    """Extract a continuation token from a chat menu item.

    YouTube has used several shapes here over time, including direct
    ``reloadContinuationData`` and endpoint-style ``continuationCommand`` forms.
    """
    if not isinstance(item, dict):
        return None

    token_candidates = (
        multi_get(
            item, "continuation", "reloadContinuationData", "continuation"
        ),
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
        multi_get(
            item, "continuationEndpoint", "getLiveChatEndpoint", "continuation"
        ),
    )
    for token in token_candidates:
        if token:
            return token
    return None


def extract_chat_submenu_continuations(
    yt_data: dict[str, Any],
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
        for label, token in zip(
            fallback_labels, unlabeled_tokens, strict=False
        ):
            labeled.setdefault(label, token)

    return labeled


def _extract_browse_continuation_token_from_response(
    yt_info: dict[str, Any],
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

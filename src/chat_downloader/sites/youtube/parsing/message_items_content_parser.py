# SPDX-License-Identifier: MIT

"""YouTube message-item parser implementation."""

from __future__ import annotations

from functools import cache
from math import isfinite
from typing import TYPE_CHECKING, Any

from chat_downloader.sites.remap import (
    Remapper as r,  # noqa: N813 — compact table-construction alias; used as r("key", ...) throughout remapping tables
)
from chat_downloader.utils.color_utils import argb_int_to_rgba, rgba_to_hex
from chat_downloader.utils.dict_utils import move_to_dict as _move_to_dict
from chat_downloader.utils.dict_utils import multi_get, try_get_first_key
from chat_downloader.utils.json_types import (
    JSONDict,
    JSONList,
    dig,
    get_dict,
    get_list,
    get_str,
)
from chat_downloader.utils.string_utils import camel_case_split
from chat_downloader.utils.time_utils import seconds_to_time, time_to_seconds

if TYPE_CHECKING:
    from collections.abc import Mapping

_ROLE_ICON_MAP: dict[str, str] = {
    "OWNER": "is_owner",
    "MODERATOR": "is_moderator",
    "VERIFIED": "is_verified",
}

# Upper bound on nested-renderer recursion (showItemEndpoint/header chains).
# Caps stack depth so a pathologically nested item payload truncates gracefully
# instead of raising RecursionError and aborting message parsing.  Mirrors the
# _MAX_FLATTEN_DEPTH guard in utils/json_utils.py.
_MAX_ITEM_PARSE_DEPTH = 50


def _modern_attributed_text(value: JSONDict, key: str) -> JSONDict:
    """Convert modern attributed text into the classic runs representation."""
    text = get_str(get_dict(value, key), "content")
    return {"runs": [{"text": text}]} if text else {}


def _modern_avatar(value: JSONDict) -> JSONDict:
    """Convert a modern image source list into classic thumbnail metadata."""
    sources = dig(value, "authorAvatar", "image", "sources")
    thumbnails: JSONList = (
        [source for source in sources if isinstance(source, dict)]
        if isinstance(sources, list)
        else []
    )
    return {"thumbnails": thumbnails} if thumbnails else {}


def _modern_author_badges(attributed: JSONDict) -> JSONList:
    """Convert inline mobile author badge images to classic badge renderers."""
    badges: JSONList = []
    author_name = get_dict(attributed, "authorName")
    for attachment in get_list(author_name, "attachmentRuns"):
        if not isinstance(attachment, dict):
            continue
        sources = dig(
            attachment,
            "element",
            "type",
            "imageType",
            "image",
            "sources",
        )
        thumbnails: JSONList = (
            [
                source
                for source in sources
                if isinstance(source, dict) and get_str(source, "url")
            ]
            if isinstance(sources, list)
            else []
        )
        if thumbnails:
            badges.append(
                {
                    "liveChatAuthorBadgeRenderer": {
                        "customThumbnail": {"thumbnails": thumbnails},
                        "tooltip": "Member",
                    },
                }
            )
    return badges


def _modern_timestamp_usec(element: JSONDict) -> str:
    """Convert a nanosecond logging identifier to a microsecond timestamp."""
    identifier = dig(
        element,
        "newElement",
        "properties",
        "identifierProperties",
        "uniqueLoggingIdentifier",
    )
    if not isinstance(identifier, str) or len(identifier) != 19:
        return ""
    try:
        return str(int(identifier) // 1000)
    except ValueError:
        return ""


def _normalize_modern_element_item(item: JSONDict) -> JSONDict:
    """Adapt a modern mobile text-message element to the classic renderer."""
    element = get_dict(item, "elementRenderer")
    model = dig(
        element,
        "newElement",
        "type",
        "componentType",
        "model",
        "liveChatTextMessageModel",
    )
    if not isinstance(model, dict):
        return item

    attributed = dig(model, "messageData", "attributedTextData")
    if not isinstance(attributed, dict):
        return item

    compatibility = get_dict(element, "compatibilityOptions")
    renderer: JSONDict = {}
    message_id = get_str(compatibility, "liveChatId")
    author_id = get_str(compatibility, "liveChatAuthorExternalChannelId")
    author_name = get_str(get_dict(attributed, "authorName"), "content").strip()
    timestamp_usec = _modern_timestamp_usec(element)
    message = _modern_attributed_text(attributed, "contentText")
    author_photo = _modern_avatar(get_dict(model, "messageData"))
    author_badges = _modern_author_badges(attributed)

    if message_id:
        renderer["id"] = message_id
    if author_id:
        renderer["authorExternalChannelId"] = author_id
    if author_name:
        renderer["authorName"] = {"simpleText": author_name}
    if timestamp_usec:
        renderer["timestampUsec"] = timestamp_usec
    if message:
        renderer["message"] = message
    if author_photo:
        renderer["authorPhoto"] = author_photo
    if author_badges:
        renderer["authorBadges"] = author_badges

    return {"liveChatTextMessageRenderer": renderer} if renderer else item


def _apply_author_roles(author: dict[str, Any]) -> None:
    """Promote badge icon types to explicit boolean role fields."""
    for badge in author.get("badges") or []:
        icon_name = (badge.get("icon_name") or "").upper()
        role_key = _ROLE_ICON_MAP.get(icon_name)
        if role_key:
            author[role_key] = True
        elif badge.get("icons"):
            author["is_sponsor"] = True


@cache
def _get_remapping() -> tuple[Mapping[str, Any], list[str]]:
    """Return the remapping table and colour-key list.

    Computed once and memoised so that the deferred import of
    ``constants_message`` does not run during package initialization (which
    would trigger a circular-import error). Tests that patch the underlying
    tables must call ``_get_remapping.cache_clear()`` first.
    """
    from chat_downloader.sites.youtube.constants_message import (
        _COLOUR_KEYS,
        build_remapping,
    )

    return build_remapping(), _COLOUR_KEYS


def _normalize_author(info: dict[str, Any]) -> None:
    """Move author fields into a sub-dict and ensure name/roles are set."""
    _move_to_dict(info, "author")
    if "author" not in info:
        return
    if "name" not in info["author"]:
        info["author"]["name"] = ""
    _apply_author_roles(info["author"])


def _reconcile_time_fields(info: dict[str, Any], offset: float) -> None:
    """Sync time_in_seconds/time_text and apply offset."""
    time_in_seconds = info.get("time_in_seconds")
    time_text = info.get("time_text")
    if time_in_seconds is not None:
        if time_text is not None:
            if time_in_seconds <= 0:
                info["time_in_seconds"] = time_to_seconds(time_text)
        else:
            info["time_text"] = seconds_to_time(time_in_seconds)
    elif time_text is not None:
        info["time_in_seconds"] = time_to_seconds(time_text)
    if offset and "time_in_seconds" in info:
        info["time_in_seconds"] -= offset
        info["time_text"] = seconds_to_time(info["time_in_seconds"])


def _apply_colour_keys(
    info: dict[str, Any],
    item_info: JSONDict,
    colour_keys: list[str],
) -> None:
    """Convert ARGB color fields to hex and store under normalized keys."""
    for colour_key in colour_keys:
        if colour_key in item_info:
            rgba_colour = argb_int_to_rgba(item_info[colour_key])  # type: ignore[arg-type]
            hex_colour = rgba_to_hex(rgba_colour)
            new_key = camel_case_split(colour_key.replace("Color", "Colour"))
            info[new_key] = hex_colour


def _merge_nested_renderers(
    info: dict[str, Any],
    item_info: JSONDict,
    depth: int,
    *,
    preserve_wrapper_time: bool,
) -> None:
    """Recursively merge showItemEndpoint and header renderers into *info*."""
    wrapper_time = info.get("time_in_seconds")
    merged_nested_renderer = False
    item_endpoint = get_dict(item_info, "showItemEndpoint")
    if item_endpoint:
        renderer = multi_get(item_endpoint, "showLiveChatItemEndpoint", "renderer")
        if renderer:
            nested_info = _parse_item(renderer, depth=depth + 1)
            if nested_info:
                info.update(nested_info)
                merged_nested_renderer = True

    header = get_dict(item_info, "header")
    if header:
        nested_info = _parse_item(header, depth=depth + 1)
        if nested_info:
            info.update(nested_info)
            merged_nested_renderer = True

    # Positive replay wrappers carry millisecond precision; zero is the
    # provider's preroll floor, where nested renderers can retain signed time.
    if (
        preserve_wrapper_time
        and merged_nested_renderer
        and isinstance(wrapper_time, (int, float))
        and not isinstance(wrapper_time, bool)
        and isfinite(wrapper_time)
        and wrapper_time > 0
    ):
        info["time_in_seconds"] = wrapper_time
        info["time_text"] = seconds_to_time(wrapper_time)


def _parse_item(
    item: JSONDict,
    info: dict[str, Any] | None = None,
    offset: float = 0,
    depth: int = 0,
    *,
    preserve_wrapper_time: bool = False,
) -> dict[str, Any]:
    """Parse a YouTube chat item recursively."""
    if info is None:
        info = {}

    if depth > _MAX_ITEM_PARSE_DEPTH:
        return info

    item_index = try_get_first_key(item)
    item_info = get_dict(item, item_index) if item_index else {}
    if not item_info:
        return info

    remapping, colour_keys = _get_remapping()

    for key, value in item_info.items():
        r.remap(info, remapping, key, value)

    _apply_colour_keys(info, item_info, colour_keys)
    _merge_nested_renderers(
        info,
        item_info,
        depth,
        preserve_wrapper_time=preserve_wrapper_time,
    )
    _normalize_author(info)
    _reconcile_time_fields(info, offset)

    if "message" not in info:
        info["message"] = None

    return info

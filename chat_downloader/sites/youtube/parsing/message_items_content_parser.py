# SPDX-License-Identifier: MIT

"""YouTube message-item parser implementation."""

from collections.abc import Mapping
from typing import Any

from chat_downloader.sites.remap import Remapper as r
from chat_downloader.utils.color_utils import argb_int_to_rgba, rgba_to_hex
from chat_downloader.utils.dict_utils import move_to_dict as _move_to_dict
from chat_downloader.utils.dict_utils import multi_get, try_get_first_key
from chat_downloader.utils.string_utils import camel_case_split
from chat_downloader.utils.time_utils import seconds_to_time, time_to_seconds

_ROLE_ICON_MAP: dict[str, str] = {
    "OWNER": "is_owner",
    "MODERATOR": "is_moderator",
    "VERIFIED": "is_verified",
}

# Module-level cache for the remapping table.  Populated on first use to avoid
# a circular import at package initialisation time (constants_message imports
# back through the top-level chat_downloader package).
_REMAPPING: Mapping[str, Any] | None = None
_COLOUR_KEYS: list[str] | None = None


def _apply_author_roles(author: dict[str, Any]) -> None:
    """Promote badge icon types to explicit boolean role fields."""
    for badge in author.get("badges") or []:
        icon_name = (badge.get("icon_name") or "").upper()
        role_key = _ROLE_ICON_MAP.get(icon_name)
        if role_key:
            author[role_key] = True
        elif badge.get("icons"):
            author["is_sponsor"] = True


def _get_remapping() -> tuple[dict[str, Any], list[str]]:
    """Return the module-level remapping table and colour-key list.

    Initialised once on first call so that the deferred import of
    ``constants_message`` does not run during package initialisation (which
    would trigger a circular-import error).
    """
    global _REMAPPING, _COLOUR_KEYS
    if _REMAPPING is None:
        from chat_downloader.sites.youtube.constants_message import (
            _COLOUR_KEYS as ck,
        )
        from chat_downloader.sites.youtube.constants_message import (
            build_remapping,
        )

        _REMAPPING = build_remapping()
        _COLOUR_KEYS = ck
    return _REMAPPING, _COLOUR_KEYS  # type: ignore[return-value]


def _parse_item(
    item: dict[str, Any],
    info: dict[str, Any] | None = None,
    offset: float = 0,
) -> dict[str, Any]:
    """Parse a YouTube chat item recursively."""
    if info is None:
        info = {}

    item_index = try_get_first_key(item)
    item_info = item.get(item_index)
    if not item_info:
        return info

    remapping, colour_keys = _get_remapping()

    for key in item_info:
        r.remap(info, remapping, key, item_info[key])

    for colour_key in colour_keys:
        if colour_key in item_info:
            rgba_colour = argb_int_to_rgba(item_info[colour_key])
            hex_colour = rgba_to_hex(rgba_colour)
            new_key = camel_case_split(colour_key.replace("Color", "Colour"))
            info[new_key] = hex_colour

    item_endpoint = item_info.get("showItemEndpoint")
    if item_endpoint:
        renderer = multi_get(
            item_endpoint, "showLiveChatItemEndpoint", "renderer"
        )
        if renderer:
            info.update(_parse_item(renderer, offset=offset))

    header = item_info.get("header")
    if header:
        info.update(_parse_item(header, offset=offset))

    _move_to_dict(info, "author")
    if "author" in info:
        if "name" not in info["author"]:
            info["author"]["name"] = ""
        _apply_author_roles(info["author"])

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

    if "message" not in info:
        info["message"] = None

    return info

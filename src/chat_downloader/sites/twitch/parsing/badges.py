# SPDX-License-Identifier: MIT

"""Badge parsing helpers for Twitch IRC and GraphQL payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.sites.models import Image
from chat_downloader.sites.twitch.constants import BADGE_KEYS
from chat_downloader.utils.conversion_utils import int_or_none
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.string_utils import replace_with_underscores

if TYPE_CHECKING:
    from chat_downloader.sites.twitch.types import BadgeSet


def _parse_badge_info(
    name: str,
    version: str,
    channel_id: str | None = None,
    badge_set: BadgeSet | None = None,
) -> dict[str, Any]:
    """Parse badge information and retrieve badge metadata."""
    new_badge: dict[str, Any] = {
        "name": replace_with_underscores(name),
        "version": int_or_none(version, version),
    }

    if badge_set is not None:
        subscriber_info: dict[str, Any] = badge_set.channel_badges
        global_info: dict[Any, Any] = badge_set.global_badges
    else:
        subscriber_info = {}
        global_info = {}

    new_badge_info = None
    if channel_id is not None:
        new_badge_info = multi_get(subscriber_info, str(channel_id), (name, version))

    if not new_badge_info:
        new_badge_info = multi_get(global_info, (name, version))

    if new_badge_info:
        for key in BADGE_KEYS:
            new_badge[key] = new_badge_info.get(key)

        image_urls = [(new_badge.pop(f"image{i}x", ""), i * 18) for i in (1, 2, 4)]

        icons: list[dict[str, Any]] = []
        for image_url, size in image_urls:
            icons.append(Image(str(image_url), size, size).json())
        new_badge["icons"] = icons

    return new_badge


def _parse_irc_badges(
    badges: str,
    channel_id: str,
    badge_set: BadgeSet | None = None,
) -> list[dict[str, Any]]:
    """Parse IRC badge string into list of badge dictionaries."""
    info: list[dict[str, Any]] = []
    if not badges:
        return info

    for badge in badges.split(","):
        split = badge.split("/", 1)
        key_length = len(split)
        if key_length == 1:
            split.append("")

        info.append(_parse_badge_info(split[0], split[1], channel_id, badge_set))
    return info

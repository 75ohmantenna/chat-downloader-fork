# SPDX-License-Identifier: MIT

"""Normalize scalar, timestamp, and author fields shared by Kick events."""

from __future__ import annotations

import contextlib
from copy import deepcopy
from typing import TYPE_CHECKING

from chat_downloader.utils.json_types import get_str
from chat_downloader.utils.time_utils import timestamp_to_microseconds

if TYPE_CHECKING:
    from collections.abc import Callable


def _opt_str(value: object) -> str | None:
    """Stringify a present value and preserve ``None`` as absent."""
    return None if value is None else str(value)


def _parse_legacy_badge(raw: object) -> dict[str, object]:
    """Convert one legacy Kick badge while preserving its existing output."""
    if not isinstance(raw, dict):
        return {}
    badge: dict[str, object] = {}
    name = _opt_str(raw.get("type"))
    if name is not None:
        badge["name"] = name
    title = _opt_str(raw.get("text"))
    if title is not None:
        badge["title"] = title
    count = raw.get("count")
    if isinstance(count, int):
        badge["count"] = count
    return badge


def _parse_v2_badge(raw: object) -> dict[str, object]:
    """Convert one image-backed Kick identity badge when it is well formed."""
    if not isinstance(raw, dict):
        return {}

    selected = raw.get("selected")
    active = raw.get("active")
    if selected is False or (not isinstance(selected, bool) and active is False):
        return {}

    badge: dict[str, object] = {}
    name = get_str(raw, "name")
    if not name.strip():
        return {}
    badge["name"] = name
    badge_type = get_str(raw, "badge_type")
    if badge_type:
        badge["badge_type"] = badge_type
    image_url = get_str(raw, "image_url")
    if image_url:
        badge["icons"] = [{"url": image_url}]
    if isinstance(selected, bool):
        badge["selected"] = selected
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        badge["metadata"] = deepcopy(metadata)
    sort_order = raw.get("sort_order")
    if isinstance(sort_order, int) and not isinstance(sort_order, bool):
        badge["sort_order"] = sort_order
    return badge


type _BadgeEntry = tuple[int, dict[str, object]]


def _parse_badge_entries(
    raw_badges: object,
    parser: Callable[[object], dict[str, object]],
) -> list[_BadgeEntry]:
    """Parse badges while retaining provider order solely for merged sorting."""
    if not isinstance(raw_badges, list):
        return []

    entries: list[_BadgeEntry] = []
    for raw in raw_badges:
        badge = parser(raw)
        if not badge:
            continue
        sort_order = raw.get("sort_order") if isinstance(raw, dict) else None
        order = (
            sort_order
            if isinstance(sort_order, int) and not isinstance(sort_order, bool)
            else 0
        )
        entries.append((order, badge))
    return entries


def _parse_badges(
    raw_badges: object,
    raw_badges_v2: object,
) -> list[dict[str, object]]:
    """Merge both Kick identity badge versions in stable provider order."""
    legacy_entries = _parse_badge_entries(raw_badges, _parse_legacy_badge)
    modern_entries = _parse_badge_entries(raw_badges_v2, _parse_v2_badge)
    if not modern_entries:
        return [badge for _, badge in legacy_entries]
    entries = [*legacy_entries, *modern_entries]
    entries.sort(key=lambda item: item[0])
    return [badge for _, badge in entries]


def _parse_author(raw_sender: object) -> dict[str, object]:
    """Build the normalized author dictionary from a Kick sender."""
    if not isinstance(raw_sender, dict):
        return {}

    author: dict[str, object] = {}
    author_id = _opt_str(raw_sender.get("id"))
    if author_id is not None:
        author["id"] = author_id

    username = _opt_str(raw_sender.get("username"))
    slug = _opt_str(raw_sender.get("slug"))
    if username is not None:
        author["display_name"] = username
    name = slug or (username.lower() if username is not None else None)
    if name is not None:
        author["name"] = name

    identity = raw_sender.get("identity")
    if isinstance(identity, dict):
        colour = _opt_str(identity.get("color"))
        if colour:
            author["colour"] = colour
        badges = _parse_badges(
            identity.get("badges"),
            identity.get("badges_v2"),
        )
        if badges:
            author["badges"] = badges

    return author


def _parse_timestamp(value: object) -> int | None:
    """Return a Kick timestamp in microseconds, or ``None`` if invalid."""
    if not isinstance(value, str) or not value:
        return None
    with contextlib.suppress(ValueError, TypeError):
        return timestamp_to_microseconds(value)
    return None

# SPDX-License-Identifier: MIT

"""Normalize scalar, timestamp, and author fields shared by Kick events."""

from __future__ import annotations

import contextlib

from chat_downloader.utils.time_utils import timestamp_to_microseconds


def _opt_str(value: object) -> str | None:
    """Stringify a present value and preserve ``None`` as absent."""
    return None if value is None else str(value)


def _parse_badges(raw_badges: object) -> list[dict[str, object]]:
    """Convert Kick identity badges into normalized dictionaries."""
    if not isinstance(raw_badges, list):
        return []

    badges: list[dict[str, object]] = []
    for raw in raw_badges:
        if not isinstance(raw, dict):
            continue
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
        if badge:
            badges.append(badge)
    return badges


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
        badges = _parse_badges(identity.get("badges"))
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

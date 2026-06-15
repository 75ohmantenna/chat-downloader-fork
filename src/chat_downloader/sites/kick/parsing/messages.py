# SPDX-License-Identifier: MIT

r"""Normalize Kick chat messages into the project's message schema.

Handles both live ``App\Events\ChatMessageEvent`` payloads and preloaded
history objects, which share the same shape.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.constants import (
    DEFAULT_MESSAGE_TYPE,
    MESSAGE_TYPE_REMAPPING,
)
from chat_downloader.sites.kick.parsing.emotes import parse_emotes
from chat_downloader.utils.time_utils import timestamp_to_microseconds

if TYPE_CHECKING:
    from collections.abc import Iterable


def _opt_str(value: Any) -> str | None:
    """Stringify a present value, returning ``None`` when it is absent.

    Unlike ``str_or_none``, a ``None`` input maps to ``None`` (not the string
    ``"None"``), so optional fields are omitted rather than fabricated.

    Args:
        value: The raw value (possibly ``None``).

    Returns:
        ``str(value)`` when ``value`` is not ``None``, otherwise ``None``.
    """
    return None if value is None else str(value)


def _parse_badges(raw_badges: Any) -> list[dict[str, Any]]:
    """Convert Kick identity badges into normalized badge dictionaries.

    Args:
        raw_badges: The ``sender.identity.badges`` value, expected to be a list
            of objects like ``{"type": ..., "text": ..., "count": ...}``.

    Returns:
        A list of normalized badge dictionaries. Unknown or malformed entries
        are skipped rather than raising.
    """
    if not isinstance(raw_badges, list):
        return []

    badges: list[dict[str, Any]] = []
    for raw in raw_badges:
        if not isinstance(raw, dict):
            continue
        badge: dict[str, Any] = {}
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


def _parse_author(raw_sender: Any) -> dict[str, Any]:
    """Build the normalized ``author`` sub-dictionary from a Kick sender.

    Args:
        raw_sender: The ``sender`` object from a Kick chat message.

    Returns:
        A normalized author dictionary (possibly partial when fields are
        absent).
    """
    if not isinstance(raw_sender, dict):
        return {}

    author: dict[str, Any] = {}
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


def parse_chat_message(raw: Any) -> dict[str, Any]:
    """Normalize one Kick chat message object.

    Args:
        raw: A decoded Kick chat message object (from the websocket event or
            preloaded history).

    Returns:
        A normalized message dictionary compatible with the output pipeline.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an id, which would
            make deduplication and output unsafe.
    """
    if not isinstance(raw, dict):
        msg = "Kick chat message payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick chat message payload was missing an id."
        raise ParsingError(msg)

    content = raw.get("content")
    text, emotes = parse_emotes(content if isinstance(content, str) else "")

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": MESSAGE_TYPE_REMAPPING.get(
            str(raw.get("type")), DEFAULT_MESSAGE_TYPE
        ),
        "message": text,
    }

    created_at = raw.get("created_at")
    if isinstance(created_at, str) and created_at:
        with contextlib.suppress(ValueError, TypeError):
            info["timestamp"] = timestamp_to_microseconds(created_at)

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    if emotes:
        info["emotes"] = emotes

    return info


def parse_preloaded_messages(raw_messages: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize a batch of preloaded history messages, skipping bad entries.

    Args:
        raw_messages: Raw preloaded message objects.

    Returns:
        Normalized message dictionaries; entries that fail to parse are
        skipped.
    """
    parsed: list[dict[str, Any]] = []
    for raw in raw_messages:
        try:
            parsed.append(parse_chat_message(raw))
        except ParsingError:
            continue
    return parsed

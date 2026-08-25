# SPDX-License-Identifier: MIT

r"""Normalize Kick chat messages into the project's message schema.

Handles both live ``App\Events\ChatMessageEvent`` payloads and preloaded
history objects, which share the same shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.constants import (
    DEFAULT_MESSAGE_TYPE,
    MESSAGE_TYPE_REMAPPING,
)
from chat_downloader.sites.kick.parsing.common_fields import (
    _opt_str,
    _parse_author,
    _parse_timestamp,
)
from chat_downloader.sites.kick.parsing.emotes import parse_emotes

if TYPE_CHECKING:
    from collections.abc import Iterable


def parse_chat_message(raw: object) -> dict[str, Any]:
    """Normalize one Kick chat message object.

    Args:
        raw: A decoded Kick chat message object (from the WebSocket event or
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

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    if emotes:
        info["emotes"] = emotes

    return info


def parse_preloaded_messages(raw_messages: Iterable[object]) -> list[dict[str, Any]]:
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

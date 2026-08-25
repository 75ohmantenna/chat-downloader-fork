# SPDX-License-Identifier: MIT

r"""Normalize Kick chat messages into the project's message schema.

Handles both live ``App\Events\ChatMessageEvent`` payloads and preloaded
history objects, which share the same shape.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.kick.constants import (
    DEFAULT_MESSAGE_TYPE,
    KICK_DEBUG_SAMPLE_LIMIT,
    MESSAGE_TYPE_REMAPPING,
)
from chat_downloader.sites.kick.parsing.common_fields import (
    _opt_str,
    _parse_author,
    _parse_timestamp,
)
from chat_downloader.sites.kick.parsing.emotes import parse_emotes
from chat_downloader.utils.json_types import get_dict

if TYPE_CHECKING:
    from collections.abc import Iterable

    from chat_downloader.utils.json_types import JSONDict


def _decode_metadata(raw_metadata: object) -> JSONDict:
    """Decode Kick metadata supplied as either an object or JSON string."""
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if not isinstance(raw_metadata, str):
        return {}
    try:
        decoded: object = json.loads(raw_metadata)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_reply_context(raw: JSONDict) -> dict[str, object]:
    """Normalize Kick reply metadata into the shared ``in_reply_to`` shape."""
    if raw.get("type") != "reply":
        return {}

    metadata = _decode_metadata(raw.get("metadata"))
    original_message = get_dict(metadata, "original_message")
    original_sender = get_dict(metadata, "original_sender")
    if not original_sender:
        original_sender = get_dict(original_message, "sender")

    reply: dict[str, object] = {}
    message_id = _opt_str(original_message.get("id"))
    if message_id:
        reply["message_id"] = message_id

    content = original_message.get("content")
    if isinstance(content, str):
        message, emotes = parse_emotes(content)
        reply["message"] = message
        if emotes:
            reply["emotes"] = emotes

    timestamp = _parse_timestamp(original_message.get("created_at"))
    if timestamp is not None:
        reply["timestamp"] = timestamp

    author = _parse_author(original_sender)
    if author:
        reply["author"] = author

    thread_parent_id = _opt_str(raw.get("thread_parent_id"))
    if thread_parent_id:
        reply["thread_parent_message_id"] = thread_parent_id

    return reply


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

    raw_message_type = str(raw.get("type"))
    message_type = MESSAGE_TYPE_REMAPPING.get(raw_message_type)
    if message_type is None:
        capture_debug_sample(
            "kick-unknown-message-type",
            {"raw": raw, "message_type": raw_message_type},
            sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        message_type = DEFAULT_MESSAGE_TYPE

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": message_type,
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

    in_reply_to = _parse_reply_context(raw)
    if in_reply_to:
        info["in_reply_to"] = in_reply_to

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
        except ParsingError as error:
            capture_debug_sample(
                "kick-malformed-preloaded-message",
                {"raw": raw, "error": str(error)},
                sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
            )
            continue
    return parsed

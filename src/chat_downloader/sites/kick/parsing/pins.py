# SPDX-License-Identifier: MIT

r"""Normalize Kick pinned-message events: create and delete.

Handles ``App\Events\PinnedMessageCreatedEvent`` and
``App\Events\PinnedMessageDeletedEvent`` Pusher payloads.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.common_fields import (
    _opt_str,
    _parse_author,
    _parse_timestamp,
)

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict


def _extract_pinned_message(
    raw_message: object, info: dict[str, Any], metadata: dict[str, object]
) -> None:
    """Extract pinned message fields into info and metadata dicts.

    Args:
        raw_message: The ``message`` sub-object from a pinned-message event.
        info: The info dict to update with message content.
        metadata: The metadata dict to update with pin-specific fields.
    """
    if not isinstance(raw_message, dict):
        return

    pinned_content = raw_message.get("content")
    if isinstance(pinned_content, str):
        info["message"] = pinned_content

    pinned_msg_id = _opt_str(raw_message.get("id"))
    if pinned_msg_id is not None:
        metadata["pinned_message_id"] = pinned_msg_id

    author = _parse_author(raw_message.get("sender"))
    if author:
        info["author"] = author

    pinned_created_at = _parse_timestamp(raw_message.get("created_at"))
    if pinned_created_at is not None:
        metadata["pinned_message_created_at"] = pinned_created_at


def _resolve_event_id(raw: JSONDict, prefix: str) -> str | None:
    """Return an explicit event id or a namespaced nested-message fallback."""
    event_id = _opt_str(raw.get("id"))
    if event_id:
        return event_id

    raw_message = raw.get("message")
    if not isinstance(raw_message, dict):
        return None
    nested_id = _opt_str(raw_message.get("id"))
    return f"kick-{prefix}:{nested_id}" if nested_id else None


def _extract_pinner(raw: JSONDict, metadata: dict[str, object]) -> None:
    """Preserve the actor that pinned the message across live/REST spellings."""
    raw_pinner = raw.get("pinnedBy")
    if not isinstance(raw_pinner, dict):
        raw_pinner = raw.get("pinned_by")
    pinner = _parse_author(raw_pinner)
    if pinner:
        metadata["pinned_by"] = pinner


def parse_pinned_message_created_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick pinned-message-created event.

    Args:
        raw: The decoded ``PinnedMessageCreatedEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"pinned_message"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick pinned-message-created event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _resolve_event_id(raw, "pin")
    if message_id is None:
        msg = "Kick pinned-message-created event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "pinned_message",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    metadata: dict[str, object] = {}

    _extract_pinned_message(raw.get("message"), info, metadata)
    _extract_pinner(raw, metadata)

    duration = raw.get("duration")
    if duration is not None:
        with contextlib.suppress(ValueError, TypeError):
            metadata["duration"] = int(duration)

    finish_at = _parse_timestamp(raw.get("finish_at"))
    if finish_at is not None:
        metadata["pinned_message_expires_at"] = finish_at

    if metadata:
        info["metadata"] = metadata

    return info


def parse_pinned_message_deleted_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick pinned-message-deleted event.

    Args:
        raw: The decoded ``PinnedMessageDeletedEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"pinned_message_deleted"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick pinned-message-deleted event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _resolve_event_id(raw, "unpin")
    if message_id is None:
        msg = "Kick pinned-message-deleted event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "pinned_message_deleted",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    raw_message = raw.get("message")
    if isinstance(raw_message, dict):
        unpinned_msg_id = _opt_str(raw_message.get("id"))
        if unpinned_msg_id is not None:
            info["metadata"] = {"unpinned_message_id": unpinned_msg_id}

    return info

# SPDX-License-Identifier: MIT

r"""Normalize Kick moderation events: ban, unban, message-delete, and chat-clear.

Handles ``App\Events\UserBannedEvent``, ``App\Events\UserUnbannedEvent``,
``App\Events\MessageDeletedEvent``, and
``App\Events\ChatClearMessagesEvent`` Pusher payloads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.common_fields import (
    _opt_str,
    _parse_timestamp,
)
from chat_downloader.utils.json_types import get_bool, get_int, get_list

if TYPE_CHECKING:
    from collections.abc import Mapping


def _parse_moderator(raw_mod: object) -> dict[str, Any]:
    """Extract a minimal moderator/user reference.

    Args:
        raw_mod: A dict with ``id`` and ``username`` keys.

    Returns:
        A dict containing ``id`` and ``username`` when present.
    """
    result: dict[str, Any] = {}
    if not isinstance(raw_mod, dict):
        return result

    mod_id = _opt_str(raw_mod.get("id"))
    if mod_id is not None:
        result["id"] = mod_id

    username = _opt_str(raw_mod.get("username"))
    if username is not None:
        result["username"] = username

    return result


def _parse_ban_timing(raw: Mapping[str, object]) -> dict[str, object]:
    """Normalize optional temporary/permanent ban timing metadata."""
    metadata: dict[str, object] = {}
    expires_at = raw.get("expires_at")
    if expires_at is not None:
        if isinstance(expires_at, str) and expires_at:
            parsed_expires_at = _parse_timestamp(expires_at)
            if parsed_expires_at is not None:
                metadata["expires_at"] = parsed_expires_at
        else:
            metadata["expires_at"] = expires_at

    duration = get_int(raw, "duration", -1)
    if duration >= 0:
        metadata["duration"] = duration

    if isinstance(raw.get("permanent"), bool):
        metadata["permanent"] = get_bool(raw, "permanent")
    return metadata


def parse_user_banned_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick user-banned event.

    Args:
        raw: The decoded ``UserBannedEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"user_banned"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick user-banned event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick user-banned event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "user_banned",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    metadata: dict[str, Any] = {}

    target = _parse_moderator(raw.get("user"))
    if target:
        metadata["user"] = target

    banned_by = _parse_moderator(raw.get("banned_by"))
    if banned_by:
        metadata["banned_by"] = banned_by

    metadata.update(_parse_ban_timing(raw))

    if metadata:
        info["metadata"] = metadata

    return info


def parse_user_unbanned_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick user-unbanned event.

    Args:
        raw: The decoded ``UserUnbannedEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"user_unbanned"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick user-unbanned event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick user-unbanned event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "user_unbanned",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    metadata: dict[str, Any] = {}

    target = _parse_moderator(raw.get("user"))
    if target:
        metadata["user"] = target

    unbanned_by = _parse_moderator(raw.get("unbanned_by"))
    if unbanned_by:
        metadata["unbanned_by"] = unbanned_by

    if metadata:
        info["metadata"] = metadata

    return info


def parse_message_deleted_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick message-deleted event.

    Args:
        raw: The decoded ``MessageDeletedEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"message_deleted"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick message-deleted event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick message-deleted event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "message_deleted",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    metadata: dict[str, object] = {}

    raw_message = raw.get("message")
    if isinstance(raw_message, dict):
        deleted_msg_id = _opt_str(raw_message.get("id"))
        if deleted_msg_id is not None:
            metadata["deleted_message_id"] = deleted_msg_id

    ai_moderated = raw.get("aiModerated")
    if isinstance(ai_moderated, bool):
        metadata["ai_moderated"] = ai_moderated

    raw_rules = raw.get("violatedRules")
    if isinstance(raw_rules, list):
        metadata["violated_rules"] = [
            rule for rule in get_list(raw, "violatedRules") if isinstance(rule, str)
        ]

    if metadata:
        info["metadata"] = metadata

    return info


def parse_chat_clear_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick chat-clear event.

    Args:
        raw: The decoded ``ChatClearMessagesEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"chat_clear"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick chat-clear event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick chat-clear event payload was missing an id."
        raise ParsingError(msg)

    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "chat_clear",
        "message": "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    chatroom_id = raw.get("chatroom_id")
    if chatroom_id is not None:
        info["metadata"] = {"chatroom_id": chatroom_id}

    return info

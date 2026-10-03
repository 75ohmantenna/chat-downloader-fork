# SPDX-License-Identifier: MIT

"""Preserve public lifecycle payloads without inventing undocumented field schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ParsingError
from chat_downloader.utils.json_types import get_dict, get_int, get_list, get_str

if TYPE_CHECKING:
    from collections.abc import Mapping

PUBLIC_MESSAGE_TYPES = frozenset(
    {
        "stream_started",
        "stream_stopped",
        "chat_moved",
        "chat_settings_changed",
        "chatroom_updated",
        "reward_redeemed",
        "kicks_gifted",
        "kicks_gifted_deleted",
        "gifts_leaderboard_updated",
        "kicks_leaderboard_updated",
        "goal_created",
        "goal_updated",
        "goal_progress_updated",
        "goal_achieved",
        "goal_canceled",
        "event_participant_joined",
        "event_participant_left",
        "drops_campaign_started",
        "channel_metadata",
        "viewer_count",
        "prediction_created",
        "prediction_updated",
    }
)


def parse_public_event(
    frame: Mapping[str, object],
    payload: object,
    message_type: str,
    received_timestamp: int | None,
) -> dict[str, Any]:
    """Keep the feed, event name, and entire decoded payload on each occurrence."""
    if "data" not in frame:
        msg = "Kick public event had no usable payload."
        raise ParsingError(msg)
    if (
        not isinstance(received_timestamp, int)
        or isinstance(received_timestamp, bool)
        or received_timestamp < 0
    ):
        msg = "Kick public event requires its receive timestamp."
        raise ParsingError(msg)
    return {
        "message_type": message_type,
        "message_id": f"kick-{message_type}:{received_timestamp}",
        "received_timestamp": received_timestamp,
        "message": message_type.replace("_", " "),
        "metadata": {
            "event_name": frame.get("event"),
            "channel": frame.get("channel"),
            "data": payload,
            "source": frame.get("source", "websocket"),
        },
    }


def normalize_compact_gifts(payload: object, received_timestamp: int) -> object:
    """Normalize compact gifts while distinguishing individual delivery chunks."""
    if not isinstance(payload, dict) or payload.get("id") is not None:
        return payload
    username = get_str(payload, "gifter_username")
    recipients = get_list(payload, "gifted_usernames")
    if (
        not username
        or not recipients
        or any(not isinstance(name, str) for name in recipients)
    ):
        return payload
    chunk = get_dict(payload, "chunk_details")
    correlation = get_str(chunk, "correlation_id")
    index = get_int(chunk, "chunk_index", -1)
    identity = (
        f"{correlation}:{index}"
        if correlation and index >= 0
        else str(received_timestamp)
    )
    return {
        **payload,
        "id": f"kick-gifts:{identity}",
        "sender": {"username": username},
        "metadata": {
            "gifted_subscriptions": {
                "quantity": len(recipients),
                "recipients": recipients,
                "gifter_username": username,
            }
        },
    }

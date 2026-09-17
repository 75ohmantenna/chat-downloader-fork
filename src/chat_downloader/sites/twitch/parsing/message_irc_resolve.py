# SPDX-License-Identifier: MIT

"""Twitch IRC message-type, action-type, and room-state resolution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.twitch.constants import (
    ACTION_TYPE_REMAPPING,
    MESSAGE_TYPE_REMAPPING,
    TWITCH_DEBUG_SAMPLE_LIMIT,
)
from chat_downloader.sites.twitch.parsing.badges import _parse_irc_badges
from chat_downloader.sites.twitch.parsing.message_emotes import (
    _add_text_for_emotes,
)
from chat_downloader.utils.conversion_utils import int_or_none

if TYPE_CHECKING:
    import re

    from chat_downloader.sites.twitch.types import BadgeSet


def _parse_irc_int_flag(value: object, default: int) -> int:
    """Coerce integer/string flags; other types use the protocol default."""
    if isinstance(value, (int, str)):
        return int(value)
    return default


def _apply_subscriber_badge_metadata(
    badges: list[dict[str, Any]],
    badge_metadata: list[dict[str, Any]],
) -> None:
    """Apply subscriber badge metadata, such as month count, onto badges."""
    subscriber_badge = next((x for x in badges if x.get("name") == "subscriber"), None)
    subscriber_badge_metadata = next(
        (x for x in badge_metadata if x.get("name") == "subscriber"),
        None,
    )
    if subscriber_badge and subscriber_badge_metadata:
        subscriber_badge["months"] = int_or_none(
            subscriber_badge_metadata["version"],
            0,
        )


def _set_message_type(
    info: dict[str, Any],
    original_message_type: str,
    *,
    raw_payload: object | None = None,
) -> None:
    """Map a message type, capturing unknown payloads for drift diagnostics."""
    new_message_type = MESSAGE_TYPE_REMAPPING.get(original_message_type)

    if new_message_type:
        info["message_type"] = new_message_type
    else:
        capture_debug_sample(
            "twitch-unknown-message-type",
            {
                "raw": raw_payload,
                "message_type": original_message_type,
                "parsed": info,
            },
            sample_limit=TWITCH_DEBUG_SAMPLE_LIMIT,
        )
        debug_log(
            f"Unknown message type: {original_message_type}",
            f"Parsed data: {info}",
        )


def _resolve_irc_badges(
    info: dict[str, Any],
    channel_id: str,
    badge_set: BadgeSet | None,
) -> None:
    """Enrich receiving and source-channel badges from an explicit snapshot."""
    source_channel = str(info.get("shared_chat_source_channel_id", "")) or channel_id
    for badge_key, metadata_key, lookup_channel in (
        ("author_badges", "author_badge_metadata", channel_id),
        ("shared_chat_source_badges", "shared_chat_source_badge_info", source_channel),
    ):
        raw_badges = str(info.pop(badge_key, ""))
        raw_metadata = str(info.pop(metadata_key, ""))
        if badge_key == "author_badges" or raw_badges:
            badges = _parse_irc_badges(raw_badges, lookup_channel, badge_set)
            metadata = _parse_irc_badges(raw_metadata, lookup_channel, badge_set)
            _apply_subscriber_badge_metadata(badges, metadata)
            info[badge_key] = badges


def _resolve_irc_shared_chat_metadata(
    info: dict[str, Any],
    channel_id: str,
) -> None:
    """Mark source identity after raw badge fields have been consumed."""
    shared_chat_source_channel_id = str(info.get("shared_chat_source_channel_id", ""))
    shared_chat_source_message_id = str(info.get("shared_chat_source_message_id", ""))
    shared_chat_source_msg_id = str(info.get("shared_chat_source_msg_id", ""))
    has_shared_chat_source = bool(
        shared_chat_source_channel_id
        or shared_chat_source_message_id
        or shared_chat_source_msg_id
        or info.get("shared_chat_source_only")
    )
    if has_shared_chat_source:
        effective_source_channel_id = shared_chat_source_channel_id or channel_id
        info["is_shared_chat_message"] = True
        info["shared_chat_effective_source_channel_id"] = effective_source_channel_id
        info["shared_chat_is_cross_channel"] = (
            bool(shared_chat_source_channel_id)
            and bool(channel_id)
            and shared_chat_source_channel_id != channel_id
        )


def _resolve_irc_action_and_message_type(
    info: dict[str, Any],
    original_action_type: str,
    message_match: str | None,
    *,
    raw_payload: object | None = None,
) -> None:
    """Resolve command, message type and room modes before drift capture."""
    action_type = ACTION_TYPE_REMAPPING.get(original_action_type)
    if original_action_type:
        info["action_type"] = action_type or original_action_type
    original_message_type = info.get("message_type")
    if original_message_type:
        _set_message_type(info, original_message_type, raw_payload=raw_payload)
    else:
        info["message_type"] = info.get("action_type", "")

    if original_action_type == "CLEARCHAT" and message_match:
        info["message_type"] = "ban_user"
        info["ban_type"] = "timeout" if info.get("ban_duration") else "permanent"
        info["banned_user"] = info.pop("message", "")

    follower_only = info.get("follower_only")
    if follower_only is not None:
        minutes = _parse_irc_int_flag(follower_only, default=-1)
        info["follower_only"] = minutes >= 0
        if minutes > 0:
            info["minutes_to_follow_before_chatting"] = minutes
    slow_mode = info.get("slow_mode")
    if slow_mode is not None:
        seconds = _parse_irc_int_flag(slow_mode, default=0)
        info["slow_mode"] = seconds != 0
        if seconds:
            info["seconds_to_wait"] = seconds
    if original_action_type and not action_type:
        capture_debug_sample(
            "twitch-unknown-irc-action",
            {
                "raw": raw_payload,
                "action_type": original_action_type,
                "parsed": info,
            },
            sample_limit=TWITCH_DEBUG_SAMPLE_LIMIT,
        )
        debug_log(
            [
                f"Unknown action type: {info['action_type']}",
                original_action_type,
                info,
            ]
        )


def _resolve_irc_message_and_emotes(
    info: dict[str, Any],
    match: re.Match[str],
) -> None:
    """Strip IRC ACTION and add display names to already-parsed emotes."""
    from chat_downloader.utils.string_utils import remove_prefixes

    message_match = match.group(3)
    if message_match:
        info["message"] = remove_prefixes(message_match, "ACTION ")

        emotes = info.pop("emotes", None)
        if emotes and isinstance(emotes, list):
            _add_text_for_emotes(info["message"], emotes)
            info["emotes"] = emotes

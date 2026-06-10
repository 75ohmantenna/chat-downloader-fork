# SPDX-License-Identifier: MIT

"""Twitch IRC message-type, action-type, and room-state resolution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log
from chat_downloader.sites.twitch.constants import (
    ACTION_TYPE_REMAPPING,
    MESSAGE_TYPE_REMAPPING,
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
    """Parse an IRC flag value as an integer, returning *default* on failure.

    Args:
        value: The raw flag value (may be an int, a string, or some other type).
        default: Value returned when *value* is neither an ``int`` nor a
            ``str``.

    Returns:
        The parsed integer, or *default* when the type is unrecognised.
    """
    if isinstance(value, (int, str)):
        return int(value)
    return default


def _apply_subscriber_badge_metadata(
    badges: list[dict[str, Any]],
    badge_metadata: list[dict[str, Any]],
) -> None:
    """Apply subscriber badge metadata, such as month count, onto badges."""
    subscriber_badge = next(
        (x for x in badges if x.get("name") == "subscriber"), None
    )
    subscriber_badge_metadata = next(
        (x for x in badge_metadata if x.get("name") == "subscriber"),
        None,
    )
    if subscriber_badge and subscriber_badge_metadata:
        subscriber_badge["months"] = int_or_none(
            subscriber_badge_metadata["version"],
            0,
        )


def _set_message_type(info: dict[str, Any], original_message_type: str) -> None:
    """Set standardized message type from original type.

    Args:
        info: Message info dictionary to update
        original_message_type: Original message type from Twitch
    """
    new_message_type = MESSAGE_TYPE_REMAPPING.get(original_message_type)

    if new_message_type:
        info["message_type"] = new_message_type
    else:
        debug_log(
            f"Unknown message type: {original_message_type}",
            f"Parsed data: {info}",
        )


def _resolve_irc_badges(
    info: dict[str, Any],
    channel_id: str,
    badge_set: BadgeSet | None,
) -> None:
    """Parse main and shared-chat IRC badges with subscriber metadata.

    Pops the raw ``author_badges``, ``author_badge_metadata``,
    ``shared_chat_source_badges``, and ``shared_chat_source_badge_info`` string
    entries from *info*, replaces them with fully-parsed badge lists, and calls
    :func:`_apply_subscriber_badge_metadata` on each list.

    Args:
        info: Partially-built message dictionary, mutated in place.
        channel_id: Receiving channel ID used for badge lookup.
        badge_set: Explicit badge data snapshot passed through to
            :func:`~chat_downloader.sites.twitch.parsing.badges.
            _parse_irc_badges`.
    """
    author_badge_metadata: str = str(info.pop("author_badge_metadata", ""))
    author_badges: str = str(info.pop("author_badges", ""))

    info["author_badges"] = _parse_irc_badges(
        author_badges, channel_id, badge_set
    )
    badge_metadata = _parse_irc_badges(
        author_badge_metadata, channel_id, badge_set
    )
    _apply_subscriber_badge_metadata(info["author_badges"], badge_metadata)

    shared_chat_source_channel_id = str(
        info.get("shared_chat_source_channel_id", "")
    )
    shared_chat_source_badges: str = str(
        info.pop("shared_chat_source_badges", "")
    )
    shared_chat_source_badge_info: str = str(
        info.pop("shared_chat_source_badge_info", ""),
    )

    if shared_chat_source_badges:
        source_channel_id = shared_chat_source_channel_id or channel_id
        info["shared_chat_source_badges"] = _parse_irc_badges(
            shared_chat_source_badges,
            source_channel_id,
            badge_set,
        )
        shared_badge_metadata = _parse_irc_badges(
            shared_chat_source_badge_info,
            source_channel_id,
            badge_set,
        )
        _apply_subscriber_badge_metadata(
            info["shared_chat_source_badges"],
            shared_badge_metadata,
        )


def _resolve_irc_shared_chat_metadata(
    info: dict[str, Any],
    channel_id: str,
) -> None:
    """Assemble shared-chat fields in *info* from already-parsed tag values.

    Reads ``shared_chat_source_channel_id``, ``shared_chat_source_message_id``,
    ``shared_chat_source_msg_id``, and ``shared_chat_source_only`` from *info*
    to determine whether this is a shared-chat message and, if so, sets:

    - ``is_shared_chat_message``
    - ``shared_chat_effective_source_channel_id``
    - ``shared_chat_is_cross_channel``

    Must be called *after* :func:`_resolve_irc_badges` so that
    ``shared_chat_source_badges`` and ``shared_chat_source_badge_info`` have
    already been consumed.

    Args:
        info: Partially-built message dictionary, mutated in place.
        channel_id: Receiving channel ID (used to detect cross-channel origin).
    """
    shared_chat_source_channel_id = str(
        info.get("shared_chat_source_channel_id", "")
    )
    shared_chat_source_message_id = str(
        info.get("shared_chat_source_message_id", "")
    )
    shared_chat_source_msg_id = str(info.get("shared_chat_source_msg_id", ""))
    has_shared_chat_source = bool(
        shared_chat_source_channel_id
        or shared_chat_source_message_id
        or shared_chat_source_msg_id
        or info.get("shared_chat_source_only")
    )
    if has_shared_chat_source:
        effective_source_channel_id = (
            shared_chat_source_channel_id or channel_id
        )
        info["is_shared_chat_message"] = True
        info["shared_chat_effective_source_channel_id"] = (
            effective_source_channel_id
        )
        info["shared_chat_is_cross_channel"] = (
            bool(shared_chat_source_channel_id)
            and bool(channel_id)
            and shared_chat_source_channel_id != channel_id
        )


def _resolve_action_type(
    info: dict[str, Any], original_action_type: str
) -> None:
    """Map *original_action_type* through :data:`ACTION_TYPE_REMAPPING`.

    Stores the mapped name (or the raw value for unknowns) as
    ``info["action_type"]``.  Unknown types are logged via :func:`debug_log`.

    Args:
        info: Partially-built message dictionary, mutated in place.
        original_action_type: Raw IRC command string (e.g. ``"PRIVMSG"``).
    """
    if original_action_type:
        new_action_type = ACTION_TYPE_REMAPPING.get(original_action_type)
        if new_action_type:
            info["action_type"] = new_action_type
        else:
            # Unknown action type
            info["action_type"] = original_action_type
            debug_log(
                [
                    f"Unknown action type: {info['action_type']}",
                    original_action_type,
                    info,
                ]
            )


def _resolve_message_type(info: dict[str, Any]) -> None:
    """Map ``info["message_type"]`` via :func:`_set_message_type`.

    When no ``message_type`` tag is present, falls back to the already-resolved
    ``action_type``.

    Args:
        info: Partially-built message dictionary, mutated in place.
    """
    original_message_type = info.get("message_type")
    if original_message_type:
        _set_message_type(info, original_message_type)
    else:
        info["message_type"] = info.get("action_type", "")


def _resolve_clearchat_ban(
    info: dict[str, Any],
    original_action_type: str,
    message_match: str | None,
) -> None:
    """Rewrite a CLEARCHAT entry as a ban when a target user is present.

    When *original_action_type* is ``"CLEARCHAT"`` and *message_match* is
    truthy the entry is rewritten with ``message_type`` set to ``"ban_user"``,
    a ``ban_type`` of ``"timeout"`` or ``"permanent"``, and the target user
    moved from ``message`` to ``banned_user``.  No mutation is made when
    *message_match* is absent (i.e. a plain ``/clearchat``).

    Args:
        info: Partially-built message dictionary, mutated in place.
        original_action_type: Raw IRC command string.
        message_match: Third capture group of the IRC regex, or ``None``.
    """
    if original_action_type == "CLEARCHAT" and message_match:
        info["message_type"] = "ban_user"
        info["ban_type"] = (
            "timeout" if info.get("ban_duration") else "permanent"
        )
        info["banned_user"] = info.pop("message", "")


def _normalize_follower_only(info: dict[str, Any]) -> None:
    """Normalize ``follower_only`` to a bool and add duration when positive.

    Converts the raw ``follower_only`` tag to a boolean.  When the numeric
    value is greater than zero, also stores
    ``minutes_to_follow_before_chatting`` with that value.

    Args:
        info: Partially-built message dictionary, mutated in place.
    """
    follower_only = info.get("follower_only")
    if follower_only is not None:
        follower_only_int = _parse_irc_int_flag(follower_only, default=-1)
        info["follower_only"] = follower_only_int >= 0
        if follower_only_int > 0:
            info["minutes_to_follow_before_chatting"] = follower_only_int


def _normalize_slow_mode(info: dict[str, Any]) -> None:
    """Normalize ``slow_mode`` to a bool and add duration when non-zero.

    Converts the raw ``slow_mode`` tag to a boolean.  When the numeric value
    is non-zero, also stores ``seconds_to_wait`` with that value.

    Args:
        info: Partially-built message dictionary, mutated in place.
    """
    slow_mode = info.get("slow_mode")
    if slow_mode is not None:
        slow_mode_int = _parse_irc_int_flag(slow_mode, default=0)
        if slow_mode_int != 0:
            info["slow_mode"] = True
            info["seconds_to_wait"] = slow_mode_int
        else:
            info["slow_mode"] = False


def _resolve_irc_action_and_message_type(
    info: dict[str, Any],
    original_action_type: str,
    message_match: str | None,
) -> None:
    """Resolve action/message type, CLEARCHAT, follower-only, slow-mode.

    Performs five sequential mutations on *info*:

    1. Maps *original_action_type* via :data:`ACTION_TYPE_REMAPPING`; stores
       the result (or the raw value for unknowns) as ``action_type``.
    2. Maps the existing ``message_type`` tag via
       :func:`_set_message_type`; falls back to ``action_type`` when absent.
    3. When *original_action_type* is ``"CLEARCHAT"`` and *message_match* is
       truthy, rewrites the entry as a ban (``ban_user`` / ``timeout`` /
       ``permanent``).
    4. Normalises ``follower_only`` to a bool and, when positive, adds
       ``minutes_to_follow_before_chatting``.
    5. Normalises ``slow_mode`` to a bool and, when non-zero, adds
       ``seconds_to_wait``.

    Args:
        info: Partially-built message dictionary, mutated in place.
        original_action_type: Raw IRC command string (e.g. ``"PRIVMSG"``).
        message_match: Third capture group of the IRC regex, or ``None`` when
            the message body was absent (used by CLEARCHAT logic).
    """
    _resolve_action_type(info, original_action_type)
    _resolve_message_type(info)
    _resolve_clearchat_ban(info, original_action_type, message_match)
    _normalize_follower_only(info)
    _normalize_slow_mode(info)


def _resolve_irc_message_and_emotes(
    info: dict[str, Any],
    match: re.Match[str],
) -> None:
    """Set ``info["message"]`` from the regex match and resolve emote names.

    If the match's third capture group is non-empty the raw message text is
    stripped of the IRC ACTION prefix and stored in *info*.  Any emote list
    already present in *info* (populated by tag parsing) has display names
    added by :func:`_add_text_for_emotes`.

    Args:
        info: Partially-built message dictionary, mutated in place.
        match: Regex match object whose third group holds the message text.
    """
    from chat_downloader.utils.string_utils import remove_prefixes

    message_match = match.group(3)
    if message_match:
        info["message"] = remove_prefixes(message_match, "ACTION ")

        emotes = info.pop("emotes", None)
        if emotes and isinstance(emotes, list):
            _add_text_for_emotes(info["message"], emotes)
            info["emotes"] = emotes

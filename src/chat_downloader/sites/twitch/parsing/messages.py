# SPDX-License-Identifier: MIT

"""Twitch message parsing entry points (GraphQL VOD/clip and IRC live chat).

Orchestrates the sub-modules:
- :mod:`message_emotes`: emote image generation and text resolution
- :mod:`message_irc_resolve`: IRC action/message-type and room-state helpers

Public entry points:
- :func:`_parse_item`: parse a GraphQL VOD/clip comment node
- :func:`_parse_irc_item`: parse an IRC chat message from a regex match
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log
from chat_downloader.sites.remap import Remapper as r
from chat_downloader.sites.twitch.parsing.badges import _parse_badge_info
from chat_downloader.sites.twitch.parsing.message_emotes import (
    _generate_emote_image_list,
)
from chat_downloader.sites.twitch.parsing.message_irc_resolve import (
    _resolve_irc_action_and_message_type,
    _resolve_irc_badges,
    _resolve_irc_message_and_emotes,
    _resolve_irc_shared_chat_metadata,
    _set_message_type,
)
from chat_downloader.sites.twitch.remappings import (
    build_game_remapping,
    build_user_remapping,
)
from chat_downloader.utils.dict_utils import move_to_dict as _move_to_dict
from chat_downloader.utils.time_utils import seconds_to_time

if TYPE_CHECKING:
    import re

    from chat_downloader.sites.twitch.types import BadgeSet


def _parse_message_info(message: dict[str, Any]) -> dict[str, Any]:
    """Parse GraphQL comment message info (fragments, emotes, badges).

    Args:
        message: GraphQL message object with fragments

    Returns:
        Dictionary with parsed message information
    """
    message_info = {
        "author_colour": message.get("userColor"),
        "author_badges": message.get("userBadges") or [],
    }

    message_text = ""
    emotes = {}
    emote_locations: dict[str, list[str]] = {}

    for fragment in message["fragments"]:
        message_text += fragment["text"]

        emote = fragment.get("emote")
        if emote:
            try:
                emote_id = emote["emoteID"]
                _, *positions = emote["id"].split(";")
                begin, end = map(int, positions)
            except (ValueError, KeyError) as emote_error:
                debug_log(
                    "Skipping malformed VOD emote "
                    f"(id={emote.get('id')!r}): {emote_error}",
                )
                continue

            if emote_id not in emotes:
                emote_locations[emote_id] = []
                emotes[emote_id] = {
                    "id": emote_id,
                    "images": _generate_emote_image_list(emote_id),
                    "name": message_text[begin : end + 1],
                }

            emote_locations[emote_id].append(f"{begin}-{end}")

    message_info["message"] = message_text

    if emotes:
        for emote_id, emote_data in emotes.items():
            emote_data["locations"] = ",".join(emote_locations[emote_id])
        message_info["emotes"] = list(emotes.values())

    return message_info


def _parse_user(item: dict[str, Any] | None) -> dict[str, Any]:
    """Parse user information from GraphQL response.

    Args:
        item: User object or None

    Returns:
        Remapped user dictionary
    """
    if isinstance(item, dict):
        return r.remap_dict(item, build_user_remapping())
    return {}


def _parse_game(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Parse game information from GraphQL response.

    Args:
        item: Game object or None

    Returns:
        Remapped game dictionary or None
    """
    if isinstance(item, dict):
        return r.remap_dict(item, build_game_remapping())
    return None


def _parse_irc_tags(
    split_info: list[str],
    irc_remapping: dict[str, Any],
) -> dict[str, Any]:
    """Parse raw IRC tag string segments into a remapped info dictionary.

    Args:
        split_info: List of ``key=value`` (or bare ``key``) tag segments,
            produced by splitting the raw tag string on ``";"``.
        irc_remapping: Remapping table produced by
            :func:`~chat_downloader.sites.twitch.constants.build_irc_remapping`.

    Returns:
        Dictionary of remapped tag key/value pairs.
    """
    info: dict[str, Any] = {}
    for item in split_info:
        keys = item.split("=", 1)
        if len(keys) == 1:
            # If there's no equals, we assign the tag a value of true.
            keys.append("1")  # Use "1" string instead of True for type safety
        r.remap(
            info,
            irc_remapping,
            keys[0],
            keys[1],
            keep_unknown_keys=True,
            replace_char_with_underscores="-",
        )
    return info


def _parse_item(
    item: dict[str, Any],
    offset: float,
    channel_id: str | None = None,
    badge_set: BadgeSet | None = None,
) -> dict[str, Any]:
    """Parse VOD/Clip comment item from GraphQL response.

    Args:
        item: Comment node from GraphQL
        offset: Time offset for clips
        channel_id: Channel ID for badge lookup
        badge_set: Explicit badge data snapshot.  When provided, module
            globals are ignored entirely.

    Returns:
        Parsed comment dictionary
    """
    from chat_downloader.sites.twitch.remappings import build_comment_remapping

    comment_remapping = build_comment_remapping()

    info: dict[str, Any] = {}

    for key, value in item.items():
        r.remap(info, comment_remapping, key, value)

    if "time_in_seconds" in info:
        info["time_in_seconds"] -= offset
        info["time_text"] = seconds_to_time(int(info["time_in_seconds"]))

    badges = info.pop("author_badges", None)
    if badges:
        info["author"]["badges"] = [
            _parse_badge_info(
                x.get("setID"), x.get("version"), channel_id, badge_set
            )
            for x in badges
            if x.get("setID") and x.get("version")
        ]
        if not info["author"]["badges"]:
            del info["author"]["badges"]

    _move_to_dict(info, "author")

    original_message_type = info.get("message_type")
    if original_message_type:
        _set_message_type(info, original_message_type)
    else:
        info["message_type"] = "text_message"

    return info


def _parse_irc_item(
    match: re.Match[str],
    badge_set: BadgeSet | None = None,
) -> dict[str, Any]:
    """Parse IRC message from regex match.

    Args:
        match: Regex match object with groups (tags, action, message)
        badge_set: Explicit badge data snapshot.  When provided, module
            globals are ignored entirely.

    Returns:
        Parsed IRC message dictionary
    """
    from chat_downloader.sites.twitch.remappings import build_irc_remapping

    irc_remapping = build_irc_remapping()
    info = _parse_irc_tags(match.group(1).split(";"), irc_remapping)

    message_match = match.group(3)
    _resolve_irc_message_and_emotes(info, match)

    channel_id = str(info.get("channel_id", ""))

    _resolve_irc_badges(info, channel_id, badge_set)

    _resolve_irc_shared_chat_metadata(info, channel_id)

    author_display_name = info.get("author_display_name")
    if author_display_name:
        info["author_name"] = author_display_name.lower()

    in_reply_to = _move_to_dict(info, "in_reply_to")

    _move_to_dict(in_reply_to, "author")
    _move_to_dict(info, "author")

    original_action_type = match.group(2)
    _resolve_irc_action_and_message_type(
        info, original_action_type, message_match
    )

    return info

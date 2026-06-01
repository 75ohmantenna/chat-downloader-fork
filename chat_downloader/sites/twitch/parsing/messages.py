# SPDX-License-Identifier: MIT

"""Twitch message parsing functions.

This module contains parsing functions for Twitch messages, emotes, badges,
user information, and other chat-related data. These functions are used by the
main Twitch downloader to parse GraphQL responses and IRC messages.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log
from chat_downloader.sites.models import Image
from chat_downloader.sites.remap import Remapper as r
from chat_downloader.sites.twitch.constants import (
    ACTION_TYPE_REMAPPING,
    EMOTE_REGEX,
    MESSAGE_TYPE_REMAPPING,
)
from chat_downloader.sites.twitch.parsing import tag_decoding
from chat_downloader.sites.twitch.parsing.badges import (
    _parse_badge_info,
    _parse_irc_badges,
)
from chat_downloader.sites.twitch.remappings import (
    build_game_remapping,
    build_user_remapping,
)
from chat_downloader.utils.conversion_utils import int_or_none
from chat_downloader.utils.dict_utils import move_to_dict as _move_to_dict
from chat_downloader.utils.time_utils import seconds_to_time

if TYPE_CHECKING:
    from chat_downloader.sites.twitch.types import BadgeSet

# Pre-compiled emote regex — compiled once at import time instead of on every
# _parse_emotes() call.  Using the compiled object avoids the re module's
# internal cache lookup overhead on each call.
_EMOTE_RE: re.Pattern[str] = re.compile(EMOTE_REGEX)
_EMOTE_IMAGE_THEMES = ("light", "dark")
_EMOTE_IMAGE_SIZES = (
    (28, "1.0"),
    (56, "2.0"),
    (112, "3.0"),
)


def _parse_bool(text: str) -> bool:
    """Compatibility wrapper for IRC boolean parsing."""
    return tag_decoding._parse_bool(text)


def _parse_bool_text(text: str) -> bool:
    """Compatibility wrapper for text boolean parsing."""
    return tag_decoding._parse_bool_text(text)


def _decode_pseudo_BNF(text: str) -> str:
    """Compatibility wrapper for IRC v3 pseudo-BNF decoding."""
    return tag_decoding._decode_pseudo_BNF(text)


def _parse_author_images(original_url: str) -> list[dict[str, Any]]:
    """Parse author profile images from a Twitch profile image URL.

    Args:
        original_url: Original profile image URL (300x300)

    Returns:
        List of image dictionaries with different sizes
    """
    # e.g. https://static-cdn.jtvnw.net/jtv_user_pictures/
    # 3892c956-0616-4fc9-b2fe-527b1be0b623-profile_image-300x300.png
    smaller_icon = original_url.replace("300x300", "70x70")
    return [
        Image(original_url, 300, 300).json(),
        Image(smaller_icon, 70, 70).json(),
    ]


@lru_cache(maxsize=4096)
def _generate_emote_image_list(emote_id: str) -> tuple[dict[str, Any], ...]:
    """Generate the canonical image list for a Twitch emote ID.

    The result is **cached** via :func:`functools.lru_cache` so repeated calls
    with the same *emote_id* (common during long streams) avoid rebuilding the
    six-entry image list each time.  A tuple is returned so the cached object
    is immutable at the outer level; callers must not mutate the individual
    image dicts.

    Serialisation note: Python's :mod:`json` module serialises tuples as JSON
    arrays, so downstream JSON output is identical to the previous list-based
    return value.

    Args:
        emote_id: Twitch emote ID (e.g. ``"25"`` for Kappa)

    Returns:
        Tuple of emote image dicts (6 entries: 3 sizes × 2 themes)
    """
    images = []
    for theme in _EMOTE_IMAGE_THEMES:
        for size_pixels, size_scale in _EMOTE_IMAGE_SIZES:
            image = Image(
                "https://static-cdn.jtvnw.net/emoticons/v2/"
                f"{emote_id}/default/{theme}/{size_scale}",
                size_pixels,
                size_pixels,
                f"{size_pixels}x{size_pixels}-{theme}",
            ).json()
            images.append(image)
    return tuple(images)


def _parse_emotes(text: str) -> list[dict[str, Any]]:
    """Parse emote information from IRC message tag.

    Format: <emote ID>:<first index>-<last index>,<another first>-<another
    last>/...

    Args:
        text: Emote tag text

    Returns:
        List of emote dictionaries
    """
    emotes = []
    matches = _EMOTE_RE.findall(text)

    for match in matches:
        emote_id = match[0]
        emote = {
            "id": emote_id,
            "locations": match[1].split(","),
            "images": _generate_emote_image_list(emote_id),
        }
        emotes.append(emote)

    return emotes


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
        for emote_id in emotes:
            emotes[emote_id]["locations"] = ",".join(emote_locations[emote_id])
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


def _add_text_for_emotes(
    message: str, emote_list: list[dict[str, Any]]
) -> None:
    """Add emote text/name to emote dictionaries from message.

    Args:
        message: Message text
        emote_list: List of emote dictionaries to update
    """
    for emote in emote_list:
        try:
            first_location = [int(x) for x in emote["locations"][0].split("-")]
            emote["name"] = message[first_location[0] : first_location[1] + 1]
        except (KeyError, IndexError, ValueError, TypeError):
            debug_log(f"Invalid emote: {emote}", f"Message: {message}")
            continue


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

    for key in item:
        r.remap(info, comment_remapping, key, item[key])

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


def _resolve_irc_badges(
    info: dict[str, Any],
    channel_id: str,
    badge_set: Any,
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


def _parse_irc_int_flag(value: Any, default: int) -> int:
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
        info["message"] = remove_prefixes(message_match, "\u0001ACTION ")

        emotes = info.pop("emotes", None)
        if emotes and isinstance(emotes, list):
            _add_text_for_emotes(info["message"], emotes)
            info["emotes"] = emotes


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

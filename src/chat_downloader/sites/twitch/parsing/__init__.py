# SPDX-License-Identifier: MIT

"""Twitch parsing modules.

Exports message parsing, emote handling, and badge parsing functions.
"""

from __future__ import annotations

from .badges import _parse_badge_info, _parse_irc_badges
from .message_emotes import (
    _add_text_for_emotes,
    _generate_emote_image_list,
    _parse_author_images,
    _parse_emotes,
)
from .message_irc_resolve import _set_message_type
from .messages import (
    _parse_game,
    _parse_irc_item,
    _parse_item,
    _parse_message_info,
    _parse_user,
)
from .tag_decoding import _decode_pseudo_BNF, _parse_bool, _parse_bool_text

__all__ = [
    "_add_text_for_emotes",
    "_decode_pseudo_BNF",
    # Emote parsing
    "_generate_emote_image_list",
    # Image/author parsing
    "_parse_author_images",
    # Badge parsing
    "_parse_badge_info",
    # Boolean parsing
    "_parse_bool",
    "_parse_bool_text",
    "_parse_emotes",
    "_parse_game",
    "_parse_irc_badges",
    # IRC message parsing
    "_parse_irc_item",
    # VOD/Clip comment parsing
    "_parse_item",
    # Message parsing
    "_parse_message_info",
    # User/game parsing
    "_parse_user",
    "_set_message_type",
]

# SPDX-License-Identifier: MIT

"""Known-key set builders for Twitch payload validation."""

from __future__ import annotations

from functools import cache

OPTIONAL_TWITCH_PASSTHROUGH_KEYS = {
    "animation_id",
    "msg_param_gift_theme",
    "msg_param_goal_contribution_type",
    "msg_param_goal_current_contributions",
    "msg_param_goal_description",
    "msg_param_goal_target_contributions",
    "msg_param_goal_user_contributions",
}


@cache
def build_known_comment_keys() -> set[str]:
    """Build set of known comment keys."""
    from chat_downloader.sites.base import BaseChatDownloader

    from .remappings import (
        build_comment_remapping,
        build_message_param_remapping,
    )

    comment_remapping = build_comment_remapping()
    message_param_remapping = build_message_param_remapping()

    known_keys = {
        "message",
        "time_in_seconds",
        "message_id",
        "time_text",
        "author",
        "timestamp",
        "message_type",
        "emotes",
    }
    known_keys.update(
        BaseChatDownloader.get_mapped_keys(
            {**comment_remapping, **message_param_remapping},
        ),
    )
    known_keys.update(OPTIONAL_TWITCH_PASSTHROUGH_KEYS)
    return known_keys


@cache
def build_known_irc_keys() -> set[str]:
    """Build set of known IRC keys."""
    from chat_downloader.sites.base import BaseChatDownloader

    from .remappings import build_irc_remapping

    irc_remapping = build_irc_remapping()

    known_keys = {
        "banned_user",
        "ban_type",
        "seconds_to_wait",
        "minutes_to_follow_before_chatting",
        "action_type",
        "author",
        "is_shared_chat_message",
        "in_reply_to",
        "message",
        "shared_chat_effective_source_channel_id",
        "shared_chat_is_cross_channel",
    }
    known_keys.update(BaseChatDownloader.get_mapped_keys(irc_remapping))
    known_keys.update(OPTIONAL_TWITCH_PASSTHROUGH_KEYS)
    return known_keys

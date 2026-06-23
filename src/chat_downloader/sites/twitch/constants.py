# SPDX-License-Identifier: MIT

"""Twitch chat downloader constants.

This module contains all configuration constants, URL patterns, remapping
dictionaries, and message type definitions for Twitch chat downloading.
"""

from __future__ import annotations

import re
from functools import cache

# API Configuration
CLIENT_ID = "ue6666qo983tsx6so1t0vnawi233wa"  # public client id
GQL_API_URL = "https://gql.twitch.tv/gql"

# IRC Configuration
PING_TEXT = "PING :tmi.twitch.tv"
PONG_TEXT = "PONG :tmi.twitch.tv"
IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
# Standard Twitch IRC capability request.
IRC_CAP_REQUEST = "CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership"
# Any PASS value and a justinfan* NICK grants anonymous read-only IRC access.
IRC_ANONYMOUS_PASSWORD = "SCHMOOPIIE"  # noqa: S105 — not a real password; any PASS value grants Twitch anonymous IRC access
IRC_ANONYMOUS_NICK = "justinfan67420"

# GraphQL Operation Hashes (UPDATED from patch - includes GlobalBadges)
OPERATION_HASHES = {
    "ChatList_Badges": (
        "838a7e0b47c09cac05f93ff081a9ff4f876b68f7624f0fc465fe30031e372fc2"
    ),
    "GlobalBadges": (
        "9db27e18d61ee393ccfdec8c7d90f14f9a11266298c2e5eb808550b77d7bcdf6"
    ),
    "StreamMetadata": (
        "ad022ca32220d5523d03a23cbcb5beaa1e0999889c1f8f78f9f2520dafb5cae6"
    ),
    "BrowsePage_Popular": (
        "fb60a7f9b2fe8f9c9a080f41585bd4564bea9d3030f4d7cb8ab7f9e99b1cee67"
    ),
    "ClipsCards__User": (
        "1cd671bfa12cec480499c087319f26d21925e9695d1f80225aae6a4354f23088"
    ),
    "VideoMetadata": (
        "45111672eea2e507f8ba44d101a61862f9c56b11dee09a15634cb75cb9b9084d"
    ),
    "FilterableVideoTower_Videos": (
        "67004f7881e65c297936f32c75246470629557a393788fb5a69d6d9a25a8fd5f"
    ),
    "VideoCommentsByOffsetOrCursor": (
        "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
    ),
}

# URL Validation Patterns
VALID_URLS = {
    # e.g. 'http://www.twitch.tv/riotgames/v/6528877?t=5m10s'
    "_get_chat_by_vod_id": r"""(?x)
                https?://
                    (?:
                        (?:(?:www|go|m)\.)?twitch\.tv/(?:[^/]+/v(?:ideo)?|videos)/|
                        player\.twitch\.tv/\?.*?\bvideo=v?
                    )
                    (?P<id>\d+)
                """,
    # e.g. 'https://clips.twitch.tv/FaintLightGullWholeWheat'
    "_get_chat_by_clip_id": r"""(?x)
                    https?://
                        (?:
                            clips\.twitch\.tv/(?:embed\?.*?\bclip=|(?:[^/]+/)*)|
                            (?:(?:www|go|m)\.)?twitch\.tv/[^/]+/clip/
                        )
                        (?P<id>[^/?#&]+)
                    """,
    # e.g. 'http://www.twitch.tv/shroomztv'
    "_get_chat_by_stream_id": r"""(?x)
                    https?://
                        (?:
                            (?:(?:www|go|m)\.)?twitch\.tv/|
                            player\.twitch\.tv/\?.*?\bchannel=
                        )
                        (?P<id>[^/#?]+)
                    """,
}

# Emote Configuration
EMOTE_REGEX = r"(\w+):([\d,-]+)"

# IRC Message Parsing
#
# The tag group is wrapped in an atomic group ``(?>...)`` so it commits to the
# first ``\s+:`` boundary (the prefix delimiter — IRC tags never contain a
# literal space) instead of re-expanding on every later ``\s+:`` when the rest
# of the pattern fails to match.  Without this, a crafted line lacking
# ``tmi.twitch.tv`` forces O(n^2) backtracking across the (up to 1 MiB) read
# buffer.  The atomic group keeps matching linear while producing identical
# matches for all well-formed Twitch lines.
MESSAGE_REGEX = re.compile(
    r"^@((?>.+?(?=\s+:))).*tmi\.twitch\.tv\s+(\S+)"
    r"(?:[^#\r\n]+#)?\s(?:\S+)?(?:\s:([^\r\n]*))?",
    re.MULTILINE,
)
# Groups:
# 1. Tag info
# 2. Action type
# 3. Message

# Badge Configuration
BADGE_KEYS = (
    "title",
    "image1x",
    "image2x",
    "image4x",
    "clickAction",
    "clickURL",
)

# URL Templates
TWITCH_HOME = "https://www.twitch.tv"
TWITCH_VIDEOS = "https://www.twitch.tv/videos"

# Action Type Remapping
ACTION_TYPE_REMAPPING = {
    # Tags
    "CLEARCHAT": "clear_chat",
    "CLEARMSG": "delete_message",
    "GLOBALUSERSTATE": "successful_login",
    "PRIVMSG": "text_message",
    "ROOMSTATE": "room_state",
    "USERNOTICE": "user_notice",
    "USERSTATE": "user_state",
    # Commands
    "HOSTTARGET": "host_target",
    "NOTICE": "notice",
    "RECONNECT": "reconnect",
}

# Message Group Remappings - Maps IRC msg-id values to standardized names
MESSAGE_GROUP_REMAPPINGS = {
    "messages": {
        "announcement": "announcement",
        "animated-message": "animated-message",
        "gigantified-emote-message": "gigantified-emote-message",
        "highlighted-message": "highlighted_message",
        "skip-subs-mode-message": "send_message_in_subscriber_only_mode",
    },
    "bits": {
        "bitsbadgetier": "bits_badge_tier",
    },
    "subscriptions": {
        "sub": "subscription",
        "resub": "resubscription",
        "subgift": "subscription_gift",
        "anonsubgift": "anonymous_subscription_gift",
        "anonsubmysterygift": "anonymous_mystery_subscription_gift",
        "submysterygift": "mystery_subscription_gift",
        "extendsub": "extend_subscription",
        "standardpayforward": "standard_pay_forward",
        "communitypayforward": "community_pay_forward",
        "primecommunitygiftreceived": "prime_community_gift_received",
    },
    "upgrades": {
        "primepaidupgrade": "prime_paid_upgrade",
        "giftpaidupgrade": "gift_paid_upgrade",
        "rewardgift": "reward_gift",
        "anongiftpaidupgrade": "anonymous_gift_paid_upgrade",
    },
    "raids": {"raid": "raid", "unraid": "unraid"},
    "hosts": {
        "host_on": "start_host",
        "host_off": "end_host",
        "bad_host_hosting": "bad_host_hosting",
        "bad_host_rate_exceeded": "bad_host_rate_exceeded",
        "bad_host_error": "bad_host_error",
        "hosts_remaining": "hosts_remaining",
        "not_hosting": "not_hosting",
        "host_target_went_offline": "host_target_went_offline",
    },
    "rituals": {
        "ritual": "ritual",
    },
    "room_states": {
        # Slow mode
        "slow_on": "enable_slow_mode",
        "slow_off": "disable_slow_mode",
        "already_slow_on": "slow_mode_already_on",
        "already_slow_off": "slow_mode_already_off",
        # Sub only mode
        "subs_on": "enable_subscriber_only_mode",
        "subs_off": "disable_subscriber_only_mode",
        "already_subs_on": "sub_mode_already_on",
        "already_subs_off": "sub_mode_already_off",
        # Emote only mode
        "emote_only_on": "enable_emote_only_mode",
        "emote_only_off": "disable_emote_only_mode",
        "already_emote_only_on": "emote_only_already_on",
        "already_emote_only_off": "emote_only_already_off",
        # R9K mode
        "r9k_on": "enable_r9k_mode",
        "r9k_off": "disable_r9k_mode",
        "already_r9k_on": "r9k_mode_already_on",
        "already_r9k_off": "r9k_mode_already_off",
        # Follower only mode
        "followers_on": "enable_follower_only_mode",
        "followers_on_zero": "enable_follower_only_mode",
        "followers_off": "disable_follower_only_mode",
        "already_followers_on": "follower_only_mode_already_on",
        "already_followers_on_zero": "follower_only_mode_already_on",
        "already_followers_off": "follower_only_mode_already_off",
    },
    "deleted_messages": {
        "msg_banned": "banned_message",
        "bad_delete_message_error": "bad_delete_message_error",
        "bad_delete_message_broadcaster": "bad_delete_message_broadcaster",
        "bad_delete_message_mod": "bad_delete_message_mod",
        "delete_message_success": "delete_message_success",
    },
    "bans": {
        # Ban
        "already_banned": "already_banned",
        "bad_ban_self": "bad_ban_self",
        "bad_ban_broadcaster": "bad_ban_broadcaster",
        "bad_ban_admin": "bad_ban_admin",
        "bad_ban_global_mod": "bad_ban_global_mod",
        "bad_ban_staff": "bad_ban_staff",
        "ban_success": "ban_success",
        # Unban
        "bad_unban_no_ban": "bad_unban_no_ban",
        "unban_success": "unban_success",
        "msg_channel_suspended": "channel_suspended_message",
        # Timeouts
        "timeout_success": "timeout_success",
        "bad_timeout_self": "bad_timeout_self",
        "bad_timeout_broadcaster": "bad_timeout_broadcaster",
        "bad_timeout_mod": "bad_timeout_mod",
        "bad_timeout_admin": "bad_timeout_admin",
        "bad_timeout_global_mod": "bad_timeout_global_mod",
        "bad_timeout_staff": "bad_timeout_staff",
    },
    "mods": {
        "bad_mod_banned": "bad_mod_banned",
        "bad_mod_mod": "bad_mod_mod",
        "mod_success": "mod_success",
        "bad_unmod_mod": "bad_unmod_mod",
        "unmod_success": "unmod_success",
        "no_mods": "no_mods",
        "room_mods": "room_mods",
    },
    "colours": {
        "turbo_only_color": "turbo_only_colour",
        "color_changed": "colour_changed",
    },
    "commercials": {
        "bad_commercial_error": "bad_commercial_error",
        "commercial_success": "commercial_success",
    },
    "vips": {
        "bad_vip_grantee_banned": "bad_vip_grantee_banned",
        "bad_vip_grantee_already_vip": "bad_vip_grantee_already_vip",
        "vip_success": "vip_success",
        "bad_unvip_grantee_not_vip": "bad_unvip_grantee_not_vip",
        "unvip_success": "unvip_success",
        "no_vips": "no_vips",
        "vips_success": "vips_success",
    },
    "chants": {"crowd-chant": "crowd_chant"},
    "charity": {"charity": "charity"},
    "milestones": {
        "viewermilestone": "viewermilestone",
    },
    "other": {
        "cmds_available": "cmds_available",
        "unrecognized_cmd": "unrecognized_cmd",
        "no_permission": "no_permission",
        "msg_ratelimit": "rate_limit_reached_message",
        "sharedchatnotice": "shared_chat_notice",
    },
}

# Message Groups for VOD/Clips
MESSAGE_GROUPS = {
    "messages": ["text_message"],
    "bans": ["ban_user"],
    "deleted_messages": ["delete_message"],
    "hosts": ["host_target"],
    "room_states": ["room_state"],
    "user_states": ["user_state"],
    "notices": ["user_notice", "notice", "successful_login"],
    "chants": ["crowd_chant"],
    "other": ["clear_chat", "reconnect"],
}

# Build MESSAGE_TYPE_REMAPPING and extend MESSAGE_GROUPS
MESSAGE_TYPE_REMAPPING = {}
for _message_group, _value in MESSAGE_GROUP_REMAPPINGS.items():
    MESSAGE_TYPE_REMAPPING.update(_value)

    if _message_group not in MESSAGE_GROUPS:
        MESSAGE_GROUPS[_message_group] = []
    MESSAGE_GROUPS[_message_group] += list(_value.values())


@cache
def build_known_comment_keys() -> set[str]:
    """Return the cached set of known VOD comment field keys."""
    from .validation_keys import build_known_comment_keys as _impl

    return _impl()


@cache
def build_known_irc_keys() -> set[str]:
    """Return the cached set of known IRC message field keys."""
    from .validation_keys import build_known_irc_keys as _impl

    return _impl()

# SPDX-License-Identifier: MIT

"""Remapping builder helpers for Twitch payloads."""

from __future__ import annotations

from functools import cache
from typing import Any

SUBSCRIPTION_TYPES = {
    "Prime": "Prime",
    "1000": "Tier 1",
    "2000": "Tier 2",
    "3000": "Tier 3",
}


def _parse_subscription_type(value: Any) -> str | None:
    return SUBSCRIPTION_TYPES.get(str(value))


@cache
def build_author_remapping() -> dict[str, Any]:
    """Build author remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import str_or_none
    from chat_downloader.utils.time_utils import timestamp_to_microseconds

    from .parsing.message_emotes import _parse_author_images

    return {
        "_id": r("id", str_or_none),
        "name": "name",
        "display_name": "display_name",
        "logo": r("images", _parse_author_images),
        "type": "type",
        "created_at": r("created_at", timestamp_to_microseconds),
        "bio": "bio",
    }


@cache
def build_user_remapping() -> dict[str, Any]:
    """Build user remapping."""
    return {
        "id": "id",
        "name": "name",
        "login": "name",
        "displayName": "display_name",
        "profileImageURL": "profile_image_url",
        "primaryColorHex": "colour",
    }


@cache
def build_comment_remapping() -> dict[str, Any]:
    """Build comment remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.time_utils import timestamp_to_microseconds

    from .parsing.messages import _parse_message_info, _parse_user

    return {
        "id": "message_id",
        "createdAt": r("timestamp", timestamp_to_microseconds),
        "commenter": r("author", _parse_user),
        "contentOffsetSeconds": "time_in_seconds",
        "message": r(None, _parse_message_info, to_unpack=True),
    }


@cache
def build_message_param_remapping() -> dict[str, Any]:
    """Build message parameter remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import int_or_none

    from .parsing.tag_decoding import (
        _decode_pseudo_bnf,
        _parse_bool,
        _parse_bool_text,
    )

    return {
        "msg-id": "message_type",
        "msg-param-cumulative-months": r("cumulative_months", int_or_none),
        "msg-param-months": r("months", int_or_none),
        "msg-param-displayName": "raider_display_name",
        "msg-param-login": "raider_name",
        "msg-param-viewerCount": r("number_of_raiders", int_or_none),
        "msg-param-promo-name": "promotion_name",
        "msg-param-promo-gift-total": ("number_of_gifts_given_during_promo"),
        "msg-param-recipient-id": "gift_recipient_id",
        "msg-param-recipient-user-name": "gift_recipient_display_name",
        "msg-param-recipient-display-name": ("gift_recipient_display_name"),
        "msg-param-gift-months": r("number_of_months_gifted", int_or_none),
        "msg-param-sender-login": "gifter_name",
        "msg-param-sender-name": "gifter_display_name",
        "msg-param-should-share-streak": r("user_wants_to_share_streaks", _parse_bool),
        "msg-param-streak-months": r(
            "number_of_consecutive_months_subscribed",
            int_or_none,
        ),
        "msg-param-sub-plan": r(
            "subscription_type",
            _parse_subscription_type,
        ),
        "msg-param-sub-plan-name": r("subscription_plan_name", _decode_pseudo_bnf),
        "msg-param-sub-benefit-end-month": r("sub_benefit_end_month", int_or_none),
        "msg-param-ritual-name": "ritual_name",
        "msg-param-threshold": "bits_badge_tier",
        "msg-param-multimonth-duration": r("multimonth_duration", int_or_none),
        "msg-param-multimonth-tenure": r("multimonth_tenure", int_or_none),
        "msg-param-was-gifted": r("was_gifted", _parse_bool_text),
        "msg-param-gifter-id": "gifter_id",
        "msg-param-gifter-login": "gifter_name",
        "msg-param-gifter-name": "gifter_display_name",
        "msg-param-anon-gift": r("was_anonymous_gift", _parse_bool_text),
        "msg-param-gift-month-being-redeemed": r(
            "gift_months_being_redeemed",
            int_or_none,
        ),
        "msg-param-domain": "domain",
        "msg-param-selected-count": r("selected_count", int_or_none),
        "msg-param-trigger-type": "trigger_type",
        "msg-param-total-reward-count": r("total_reward_count", int_or_none),
        "msg-param-trigger-amount": r("trigger_amount", int_or_none),
        "msg-param-origin-id": r("origin_id", _decode_pseudo_bnf),
        "msg-param-community-gift-id": "community_gift_id",
        "msg-param-sender-count": r("sender_count", int_or_none),
        "msg-param-mass-gift-count": r("mass_gift_count", int_or_none),
        "msg-param-prior-gifter-anonymous": r(
            "prior_gifter_anonymous",
            _parse_bool_text,
        ),
        "msg-param-prior-gifter-user-name": "prior_gifter_name",
        "msg-param-prior-gifter-display-name": "prior_gifter_display_name",
        "msg-param-prior-gifter-id": "prior_gifter_id",
        "msg-param-fun-string": "fun_string",
        "msg-param-charity": "charity",
        "msg-param-charity-name": r("charity_name", _decode_pseudo_bnf),
        "msg-param-donation-amount": r("donation_amount", int_or_none),
        "msg-param-donation-currency": "donation_currency",
        "msg-param-charity-hashtag": "charity_hashtag",
        "msg-param-charity-learn-more": "charity_link",
        "msg-param-charity-hours-remaining": r("charity_hours_remaining", int_or_none),
        "msg-param-charity-days-remaining": r("charity_days_remaining", int_or_none),
        "msg-param-total": r("charity_total_raised", int_or_none),
        "msg-param-category": "milestone_category",
        "msg-param-current-badge-level": r("current_badge_level", int_or_none),
        "msg-param-value": r("milestone_value", int_or_none),
        "msg-param-copoReward": r("milestone_channel_points_reward", int_or_none),
        "msg-param-id": "milestone_id",
        "msg-param-color": "announcement_colour",
        "msg-param-profileImageURL": "profile_image_url",
        "msg-param-gift-theme": "msg_param_gift_theme",
        "msg-param-goal-target-contributions": ("msg_param_goal_target_contributions"),
        "msg-param-goal-current-contributions": (
            "msg_param_goal_current_contributions"
        ),
        "msg-param-goal-user-contributions": ("msg_param_goal_user_contributions"),
        "msg-param-goal-description": "msg_param_goal_description",
        "msg-param-goal-contribution-type": ("msg_param_goal_contribution_type"),
        "msg-param-advertiser-name": r("advertiser_name", _decode_pseudo_bnf),
        "msg-param-gift-sub-match-quantity": r(
            "gift_subscription_match_quantity",
            int_or_none,
        ),
        "msg-param-breakpoint-number": r(
            "one_tap_breakpoint_number",
            int_or_none,
        ),
        "msg-param-gift-id": "one_tap_gift_id",
        "msg-param-breakpoint-threshold-bits": r(
            "one_tap_breakpoint_threshold_bits",
            int_or_none,
        ),
        "msg-param-user-display-name": "one_tap_user_display_name",
        "msg-param-bits-spent": r("one_tap_bits_spent", int_or_none),
        "msg-param-largest-contributor-count": r(
            "one_tap_largest_contributor_count",
            int_or_none,
        ),
        "msg-param-channel-display-name": "one_tap_channel_display_name",
        "msg-param-streak-size-bits": r("one_tap_streak_size_bits", int_or_none),
        "msg-param-streak-size-taps": r("one_tap_streak_size_taps", int_or_none),
        "msg-param-contributor-1": "one_tap_contributor_1",
        "msg-param-contributor-1-taps": r("one_tap_contributor_1_taps", int_or_none),
        "msg-param-contributor-2": "one_tap_contributor_2",
        "msg-param-contributor-2-taps": r("one_tap_contributor_2_taps", int_or_none),
        "msg-param-contributor-3": "one_tap_contributor_3",
        "msg-param-contributor-3-taps": r("one_tap_contributor_3_taps", int_or_none),
        "msg-param-ms-remaining": r("one_tap_ms_remaining", int_or_none),
    }


@cache
def build_irc_remapping() -> dict[str, Any]:
    """Build IRC remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import int_or_none, str_or_none

    from .parsing.message_emotes import _parse_emotes
    from .parsing.tag_decoding import _decode_pseudo_bnf, _parse_bool

    message_param_remapping = build_message_param_remapping()

    return {
        "ban-duration": r("ban_duration", int_or_none),
        "login": "author_name",
        "target-msg-id": "target_message_id",
        "emote-sets": "emote_sets",
        "color": "colour",
        "display-name": "author_display_name",
        "user-id": r("author_id", str_or_none),
        "badge-info": "author_badge_metadata",
        "badges": "author_badges",
        "bits": r("bits", int_or_none),
        "id": "message_id",
        "mod": r("author_is_moderator", _parse_bool),
        "room-id": r("channel_id", str_or_none),
        "tmi-sent-ts": r("timestamp", lambda x: int_or_none(x, 0) * 1000),
        "subscriber": r("author_is_subscriber", _parse_bool),
        "turbo": r("author_is_turbo", _parse_bool),
        "client-nonce": "client_nonce",
        "user-type": "user_type",
        "reply-parent-msg-body": r("in_reply_to_message", _decode_pseudo_bnf),
        "reply-parent-user-id": r("in_reply_to_author_id", str_or_none),
        "reply-parent-msg-id": "in_reply_to_message_id",
        "reply-parent-display-name": "in_reply_to_author_display_name",
        "reply-parent-user-login": "in_reply_to_author_name",
        "reply-thread-parent-msg-id": "reply_thread_parent_msg_id",
        "reply-thread-parent-user-id": r("reply_thread_parent_user_id", str_or_none),
        "reply-thread-parent-display-name": "reply_thread_parent_display_name",
        "reply-thread-parent-user-login": "reply_thread_parent_user_login",
        "crowd-chant-parent-msg-id": "crowd_chant_in_reply_to_message_id",
        "custom-reward-id": "custom_reward_id",
        "source-id": "shared_chat_source_message_id",
        "source-msg-id": "shared_chat_source_msg_id",
        "source-room-id": "shared_chat_source_channel_id",
        "source-badges": "shared_chat_source_badges",
        "source-badge-info": "shared_chat_source_badge_info",
        "source-only": r("shared_chat_source_only", _parse_bool),
        "emotes": r("emotes", _parse_emotes),
        "flags": "flags",
        "first-msg": r("is_first_message", _parse_bool),
        "returning-chatter": r("is_returning_chatter", _parse_bool),
        "vip": r("is_vip", _parse_bool),
        "emote-only": r("emote_only", _parse_bool),
        "followers-only": r("follower_only", int_or_none),
        "r9k": r("r9k_mode", _parse_bool),
        "slow": r("slow_mode", int_or_none),
        "subs-only": r("subscriber_only", _parse_bool),
        "rituals": r("rituals_enabled", _parse_bool),
        "system-msg": r("system_message", _decode_pseudo_bnf),
        "number-of-viewers": "number_of_viewers",
        "target-user-id": r("target_author_id", str_or_none),
        "animation-id": "animation_id",
        "pinned-chat-paid-amount": r("pinned_chat_paid_amount", int_or_none),
        "pinned-chat-paid-canonical-amount": r(
            "pinned_chat_paid_canonical_amount",
            int_or_none,
        ),
        "pinned-chat-paid-currency": "pinned_chat_paid_currency",
        "pinned-chat-paid-exponent": r("pinned_chat_paid_exponent", int_or_none),
        "pinned-chat-paid-is-system-message": r(
            "pinned_chat_paid_is_system_message",
            _parse_bool,
        ),
        "pinned-chat-paid-level": "pinned_chat_paid_level",
        **message_param_remapping,
    }


@cache
def build_game_remapping() -> dict[str, Any]:
    """Build game remapping."""
    return {
        "id": "id",
        "name": "name",
        "displayName": "display_name",
        "boxArtURL": "box_art_url",
    }


@cache
def build_clip_remapping() -> dict[str, Any]:
    """Build clip remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import str_or_none
    from chat_downloader.utils.time_utils import timestamp_to_microseconds

    from .parsing.messages import _parse_game, _parse_user

    return {
        "id": r("id", str_or_none),
        "slug": "slug",
        "url": "url",
        "embedURL": "embed_url",
        "title": "title",
        "viewCount": "views",
        "language": "language",
        "curator": r("curator", _parse_user),
        "game": r("game", _parse_game),
        "broadcaster": r("broadcaster", _parse_user),
        "thumbnailURL": "thumbnail_url",
        "createdAt": r("created_at", timestamp_to_microseconds),
        "durationSeconds": "duration",
    }


@cache
def build_video_remapping() -> dict[str, Any]:
    """Build video remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import str_or_none
    from chat_downloader.utils.time_utils import timestamp_to_microseconds

    from .parsing.messages import _parse_game, _parse_user

    return {
        "id": r("id", str_or_none),
        "animatedPreviewURL": "animated_preview_url",
        "game": r("game", _parse_game),
        "lengthSeconds": "duration",
        "owner": r("owner", _parse_user),
        "previewThumbnailURL": "preview_thumbnail_url",
        "publishedAt": r("published_at", timestamp_to_microseconds),
        "title": "title",
        "viewCount": "views",
        "resourceRestriction": "resource_restriction",
    }


@cache
def build_livestream_remapping() -> dict[str, Any]:
    """Build livestream remapping with parsing functions."""
    from chat_downloader.sites.remap import (
        Remapper as r,  # noqa: N813 — compact table-construction alias
    )
    from chat_downloader.utils.conversion_utils import str_or_none

    from .parsing.messages import _parse_game, _parse_user

    return {
        "id": r("id", str_or_none),
        "title": "title",
        "viewersCount": "viewers",
        "previewImageURL": "preview_image_url",
        "broadcaster": r("broadcaster", _parse_user),
        "game": r("game", _parse_game),
        "type": "type",
    }

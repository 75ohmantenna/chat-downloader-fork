# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.sites.remap import Remapper
from chat_downloader.sites.twitch.remappings import (
    build_author_remapping,
    build_clip_remapping,
    build_comment_remapping,
    build_game_remapping,
    build_irc_remapping,
    build_livestream_remapping,
    build_message_param_remapping,
    build_user_remapping,
    build_video_remapping,
)


def test_builder_functions_return_expected_top_level_keys() -> None:
    assert set(build_author_remapping()) >= {
        "_id",
        "name",
        "display_name",
        "logo",
        "created_at",
    }
    assert build_user_remapping()["displayName"] == "display_name"
    assert build_game_remapping()["boxArtURL"] == "box_art_url"
    assert build_comment_remapping()["id"] == "message_id"
    assert build_clip_remapping()["viewCount"] == "views"
    assert (
        build_video_remapping()["resourceRestriction"] == "resource_restriction"
    )


def test_message_param_remapping_converts_selected_fields() -> None:
    info = Remapper.remap_dict(
        {
            "msg-param-cumulative-months": "7",
            "msg-param-sub-plan": "1000",
            "msg-param-sub-plan-name": r"Tier\s1",
            "msg-param-origin-id": r"origin\svalue",
            "msg-param-color": "blue",
            "msg-param-profileImageURL": "https://example.invalid/p.png",
        },
        build_message_param_remapping(),
    )

    assert info["cumulative_months"] == 7
    assert info["subscription_type"] == "Tier 1"
    assert info["subscription_plan_name"] == "Tier 1"
    assert info["origin_id"] == "origin value"
    assert info["announcement_colour"] == "blue"
    assert info["profile_image_url"] == "https://example.invalid/p.png"


def test_irc_remapping_decodes_selected_boolean_numeric_and_text_fields() -> (
    None
):
    info = Remapper.remap_dict(
        {
            "ban-duration": "600",
            "room-id": "321",
            "source-only": "1",
            "followers-only": "15",
            "system-msg": r"Gifted\sa\ssub",
            "tmi-sent-ts": "42",
        },
        build_irc_remapping(),
    )

    assert info["ban_duration"] == 600
    assert info["channel_id"] == "321"
    assert info["shared_chat_source_only"] is True
    assert info["follower_only"] == 15
    assert info["system_message"] == "Gifted a sub"
    assert info["timestamp"] == 42000


def test_irc_remapping_captures_badges_and_message_colors_and_notice_type() -> (
    None
):
    """Notice messages keep raw badge/color fields while decoding text."""
    info = Remapper.remap_dict(
        {
            "msg-id": "sharedchatnotice",
            "msg-param-color": "PURPLE",
            "system-msg": r"Shared\sChat\snotice",
            "color": "#00AEEF",
            "badges": "moderator/1,subscriber/0",
        },
        build_irc_remapping(),
    )

    assert info["message_type"] == "sharedchatnotice"
    assert info["announcement_colour"] == "PURPLE"
    assert info["system_message"] == "Shared Chat notice"
    assert info["colour"] == "#00AEEF"
    assert info["author_badges"] == "moderator/1,subscriber/0"


def test_message_param_remapping_handles_bool_text_and_unknown_values() -> None:
    info = Remapper.remap_dict(
        {
            "msg-param-sub-plan": "5000",
            "msg-param-should-share-streak": "1",
            "msg-param-was-gifted": "false",
            "msg-param-anon-gift": "true",
            "msg-param-prior-gifter-anonymous": "true",
        },
        build_message_param_remapping(),
    )

    assert info["subscription_type"] is None
    assert info["user_wants_to_share_streaks"] is True
    assert info["was_gifted"] is False
    assert info["was_anonymous_gift"] is True
    assert info["prior_gifter_anonymous"] is True


def test_message_param_remapping_accepts_numeric_sub_plan() -> None:
    info = Remapper.remap_dict(
        {"msg-param-sub-plan": 1000},
        build_message_param_remapping(),
    )

    assert info["subscription_type"] == "Tier 1"


def test_livestream_remapping_is_cached_and_has_expected_keys() -> None:
    first = build_livestream_remapping()
    second = build_livestream_remapping()

    assert first == second
    assert set(first) == {
        "id",
        "title",
        "viewersCount",
        "previewImageURL",
        "broadcaster",
        "game",
        "type",
    }
    assert first["viewersCount"] == "viewers"
    assert first["type"] == "type"

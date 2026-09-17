# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.sites.youtube import constants_message as yt_constants
from chat_downloader.sites.youtube import parsing as yt_messages
from chat_downloader.sites.youtube.parsing import message_items_content_parser


def _thumbnails(name):
    return {
        "thumbnails": [
            {"url": f"https://img.example/{name}=s32", "width": 32, "height": 32}
        ],
    }


def _badge(title, icon=None, image=None):
    renderer = {"tooltip": title}
    if icon is not None:
        renderer["icon"] = {"iconType": icon}
    if image is not None:
        renderer["customThumbnail"] = _thumbnails(image)
    return {"liveChatAuthorBadgeRenderer": renderer}


def test_parse_item_depth_guard_returns_existing_info() -> None:
    info = {"message": "kept"}

    result = message_items_content_parser._parse_item(
        {"ignored": {}},
        info,
        depth=message_items_content_parser._MAX_ITEM_PARSE_DEPTH + 1,
    )

    assert result is info


def test_get_source_image_url_strips_query_params() -> None:
    assert (
        yt_messages._get_source_image_url("https://yt3.ggpht.com/abc=s32-c-k")
        == "https://yt3.ggpht.com/abc"
    )
    assert yt_messages._get_source_image_url("https://example.com/no_params") == (
        "https://example.com/no_params"
    )


def test_parse_youtube_link_variants() -> None:
    assert (
        yt_messages._parse_youtube_link("/redirect?q=https%3A%2F%2Fexample.com%2Fhello")
        == "https://example.com/hello"
    )
    assert (
        yt_messages._parse_youtube_link("//example.com/path")
        == "https://example.com/path"
    )
    assert (
        yt_messages._parse_youtube_link("/watch?v=abc")
        == "https://www.youtube.com/watch?v=abc"
    )
    assert (
        yt_messages._parse_youtube_link("https://example.com/x")
        == "https://example.com/x"
    )


def test_parse_navigation_endpoint_success_and_fallback() -> None:
    endpoint = {
        "commandMetadata": {"webCommandMetadata": {"url": "/watch?v=abc"}},
    }
    assert (
        yt_messages._parse_navigation_endpoint(endpoint)
        == "https://www.youtube.com/watch?v=abc"
    )
    assert (
        yt_messages._parse_navigation_endpoint({}, default_text="fallback")
        == "fallback"
    )


def test_parse_runs_plain_text_links_and_emoji() -> None:
    run_info = {
        "runs": [
            {"text": "hi "},
            {
                "text": "link",
                "navigationEndpoint": {
                    "commandMetadata": {
                        "webCommandMetadata": {"url": "https://example.com/a"},
                    },
                },
            },
            {"text": " "},
            {
                "emoji": {
                    "emojiId": "E1",
                    "shortcuts": [":)"],
                    "searchTerms": ["smile"],
                    "image": _thumbnails("e"),
                    "isCustomEmoji": True,
                },
            },
            {"text": "!"},
        ],
    }

    parsed = yt_messages._parse_runs(run_info)
    assert parsed["message"] == "hi https://example.com/a :)!"
    assert "emotes" in parsed
    assert parsed["emotes"][0]["id"] == "E1"
    assert parsed["emotes"][0]["name"] == ":)"

    assert yt_messages._parse_runs("not a dict") == {"message": ""}


def test_parse_thumbnails_list_dict_and_invalid_inputs() -> None:
    thumbs = _thumbnails("x")
    parsed = yt_messages._parse_thumbnails(thumbs)
    assert parsed[0]["id"] == "source"
    assert parsed[0]["url"] == "https://img.example/x"
    assert parsed[1]["id"] == "32x32"

    parsed2 = yt_messages._parse_thumbnails([thumbs])
    assert parsed2[0]["id"] == "source"

    assert yt_messages._parse_thumbnails("bad") == []


def test_parse_badges_extracts_tooltip_icon_and_icons() -> None:
    badge_items = [_badge("Moderator", "MODERATOR", "badge")]

    badges = yt_messages._parse_badges(badge_items)
    assert len(badges) == 1
    badge = badges[0]
    assert badge["title"] == "Moderator"
    assert badge["icon_name"] == "moderator"
    assert badge["icons"][0]["id"] == "source"
    assert badge["icons"][0]["url"] == "https://img.example/badge"
    assert badge["icons"][1]["id"] == "32x32"


@pytest.mark.parametrize(
    ("info", "offset", "seconds", "text"),
    [
        ({"time_in_seconds": 0, "time_text": "0:02"}, 0, 2, "0:02"),
        ({"time_text": "0:10"}, 5, 5, "0:05"),
    ],
    ids=["zero-time-fixup", "offset-subtraction"],
)
def test_parse_item_time_logic_and_offset_adjustment(info, offset, seconds, text):
    out = yt_messages._parse_item(
        {"liveChatTextMessageRenderer": {"dummy": 1}},
        info=info.copy(),
        offset=offset,
    )
    assert out["time_in_seconds"] == seconds
    assert out["time_text"] == text


def test_parse_item_parses_paid_message_leaderboard_badge() -> None:
    out = yt_messages._parse_item(
        {
            "liveChatPaidMessageRenderer": {
                "leaderboardBadge": _badge("Top supporter", "STAR", "leader"),
            },
        },
    )

    badge = out["leaderboard_badge"]
    assert badge["tooltip"] == "Top supporter"
    assert badge["icon"] == "STAR"
    assert badge["badge_icons"][0]["id"] == "source"
    assert badge["badge_icons"][1]["id"] == "32x32"


def test_known_keys_include_dynamic_state_data() -> None:
    assert "dynamicStateData" in yt_constants.known_keys()


@pytest.mark.parametrize(
    ("badges", "present", "absent"),
    [
        (
            [_badge("Moderator", "MODERATOR")],
            ["is_moderator"],
            ["is_owner", "is_verified", "is_sponsor"],
        ),
        (
            [_badge("Owner", "OWNER"), _badge("Verified", "VERIFIED")],
            ["is_owner", "is_verified"],
            ["is_moderator"],
        ),
        (
            [_badge("Member (6 months)", image="badge")],
            ["is_sponsor"],
            ["is_moderator"],
        ),
    ],
    ids=["moderator", "owner-and-verified", "member"],
)
def test_parse_item_sets_author_role_booleans_from_badges(badges, present, absent):
    out = yt_messages._parse_item(
        {
            "liveChatTextMessageRenderer": {
                "authorBadges": badges,
                "timestampUsec": "1",
            },
        }
    )
    for role in present:
        assert out["author"].get(role) is True
    for role in absent:
        assert role not in out["author"]

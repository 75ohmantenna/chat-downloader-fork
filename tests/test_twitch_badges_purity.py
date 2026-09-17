# SPDX-License-Identifier: MIT

"""Injected badge metadata and cache snapshot isolation."""

from __future__ import annotations

import pytest

from chat_downloader.sites.twitch.parsing.badges import (
    _parse_badge_info,
    _parse_irc_badges,
)
from chat_downloader.sites.twitch.parsing.messages import _parse_item
from chat_downloader.sites.twitch.types import BadgeCache, BadgeSet
from tests.twitch_third_helpers import (
    badge_record,
    gql_comment,
    gql_message,
    irc_frame,
    parse_irc,
)


@pytest.fixture
def badge_set():
    def badge(title):
        return badge_record(
            title,
            clickAction="visit_url",
            clickURL="https://twitch.tv",
            **{f"image{size}x": "https://example.com/img.png" for size in (1, 2, 4)},
        )

    return BadgeSet(
        global_badges={("moderator", "1"): badge("CORRECT_GLOBAL")},
        channel_badges={"999": {("subscriber", "12"): badge("CORRECT_CHANNEL")}},
    )


@pytest.mark.parametrize(
    ("name", "version", "title"),
    [("moderator", "1", "CORRECT_GLOBAL"), ("subscriber", "12", "CORRECT_CHANNEL")],
)
def test_parse_badge_info_uses_badge_set(badge_set, name, version, title):
    result = _parse_badge_info(name, version, channel_id="999", badge_set=badge_set)
    assert result["name"] == name
    assert result["title"] == title
    assert [icon["url"] for icon in result["icons"]] == [
        "https://example.com/img.png"
    ] * 3


@pytest.mark.parametrize("irc", [True, False], ids=["irc", "replay"])
def test_message_parsers_use_badge_set(badge_set, irc):
    if irc:
        raw = irc_frame(
            "badge-info=subscriber/12;badges=moderator/1,subscriber/12;"
            "color=#FF0000;id=abc123;mod=1;subscriber=1;"
            "tmi-sent-ts=1700000000000;user-type=mod"
        )
        result = parse_irc(raw, badge_set=badge_set)
    else:
        node = gql_comment(
            id="msg-001",
            createdAt="2024-01-01T00:00:00Z",
            contentOffsetSeconds=0.0,
            commenter={
                "id": "12345",
                "login": "testuser",
                "displayName": "TestUser",
                "profileImageURL": "",
                "primaryColorHex": None,
            },
            message=gql_message(
                userColor="#FF0000",
                userBadges=[{"setID": "subscriber", "version": "12"}],
            ),
        )
        result = _parse_item(node, offset=0.0, channel_id="999", badge_set=badge_set)
    assert result["author"]["badges"][0]["title"] == (
        "CORRECT_GLOBAL" if irc else "CORRECT_CHANNEL"
    )


@pytest.mark.parametrize("empty", [False, True], ids=["injected", "empty"])
def test_parse_irc_badges_uses_badge_set(badge_set, empty):
    snapshot = BadgeSet(global_badges={}, channel_badges={}) if empty else badge_set
    result = _parse_irc_badges(
        "" if empty else "moderator/1", "999", badge_set=snapshot
    )
    assert [badge["title"] for badge in result] == ([] if empty else ["CORRECT_GLOBAL"])


def test_empty_snapshot():
    snap = BadgeCache().snapshot()
    assert isinstance(snap, BadgeSet)
    assert snap.global_badges == snap.channel_badges == {}


@pytest.mark.parametrize(
    ("attribute", "key", "value", "new_key"),
    [
        ("global_badges", ("mod", "1"), {"title": "Moderator"}, ("new", "1")),
        ("channel_badges", "ch1", {("sub", "6"): {"title": "6-Month Sub"}}, "ch2"),
    ],
)
def test_snapshot_copies_current_state_without_sharing_top_level(
    attribute, key, value, new_key
):
    cache = BadgeCache()
    original = getattr(cache, attribute)
    original[key] = value
    snapshot = getattr(cache.snapshot(), attribute)
    assert snapshot[key] == value
    snapshot[new_key] = {}
    assert new_key not in original


def test_unknown_badge_has_no_icons():
    result = _parse_badge_info(
        "bits", "100", badge_set=BadgeSet(global_badges={}, channel_badges={})
    )
    assert result["name"] == "bits"
    assert "icons" not in result
    assert "title" not in result

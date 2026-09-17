# SPDX-License-Identifier: MIT

"""Injected badge metadata and cache snapshot isolation."""

from __future__ import annotations

from typing import Any

import pytest

from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.badges import (
    _parse_badge_info,
    _parse_irc_badges,
)
from chat_downloader.sites.twitch.parsing.messages import (
    _parse_irc_item,
    _parse_item,
)
from chat_downloader.sites.twitch.types import BadgeCache, BadgeSet


def _make_badge_dict(
    title: str,
    image_url: str = "https://example.com/img.png",
) -> dict[str, Any]:
    """Return a minimal badge dict in the shape stored by update_badge_info."""
    return {
        "title": title,
        "image1x": image_url,
        "image2x": image_url,
        "image4x": image_url,
        "clickAction": "visit_url",
        "clickURL": "https://twitch.tv",
    }


@pytest.fixture
def badge_set() -> BadgeSet:
    return BadgeSet(
        global_badges={("moderator", "1"): _make_badge_dict("CORRECT_GLOBAL")},
        channel_badges={
            "999": {("subscriber", "12"): _make_badge_dict("CORRECT_CHANNEL")},
        },
    )


@pytest.mark.parametrize(
    ("name", "version", "title"),
    [("moderator", "1", "CORRECT_GLOBAL"), ("subscriber", "12", "CORRECT_CHANNEL")],
)
def test_parse_badge_info_uses_badge_set(badge_set, name, version, title) -> None:
    result = _parse_badge_info(name, version, channel_id="999", badge_set=badge_set)
    assert result["name"] == name
    assert result["title"] == title
    assert [icon["url"] for icon in result["icons"]] == [
        "https://example.com/img.png",
    ] * 3


def test_parse_irc_badges_uses_badge_set(badge_set) -> None:
    results = _parse_irc_badges("moderator/1", "999", badge_set=badge_set)
    assert len(results) == 1
    assert results[0]["title"] == "CORRECT_GLOBAL"


def test_parse_irc_item_uses_badge_set(badge_set) -> None:
    raw = (
        "@badge-info=subscriber/12;badges=moderator/1,subscriber/12;"
        "color=#FF0000;display-name=TestUser;emotes=;flags=;id=abc123;"
        "mod=1;room-id=999;subscriber=1;tmi-sent-ts=1700000000000;"
        "turbo=0;user-id=12345;user-type=mod "
        ":testuser!testuser@testuser.tmi.twitch.tv PRIVMSG #channel :hello"
        "\r\n"
    )
    match = MESSAGE_REGEX.search(raw)
    assert match is not None
    result = _parse_irc_item(match, badge_set=badge_set)
    assert result["author"]["badges"][0]["title"] == "CORRECT_GLOBAL"


def test_parse_item_uses_badge_set(badge_set) -> None:
    node = {
        "id": "msg-001",
        "createdAt": "2024-01-01T00:00:00Z",
        "contentOffsetSeconds": 0.0,
        "commenter": {
            "id": "12345",
            "login": "testuser",
            "displayName": "TestUser",
            "profileImageURL": "",
            "primaryColorHex": None,
        },
        "message": {
            "userColor": "#FF0000",
            "userBadges": [{"setID": "subscriber", "version": "12"}],
            "fragments": [{"text": "hello"}],
        },
    }
    result = _parse_item(node, offset=0.0, channel_id="999", badge_set=badge_set)
    assert result["author"]["badges"][0]["title"] == "CORRECT_CHANNEL"


def test_empty_snapshot() -> None:
    cache = BadgeCache()
    snap = cache.snapshot()
    assert isinstance(snap, BadgeSet)
    assert snap.global_badges == {}
    assert snap.channel_badges == {}


@pytest.mark.parametrize(
    ("attribute", "key", "value", "new_key"),
    [
        ("global_badges", ("mod", "1"), {"title": "Moderator"}, ("new", "1")),
        ("channel_badges", "ch1", {("sub", "6"): {"title": "6-Month Sub"}}, "ch2"),
    ],
)
def test_snapshot_copies_current_state_without_sharing_top_level(
    attribute,
    key,
    value,
    new_key,
) -> None:
    cache = BadgeCache()
    original = getattr(cache, attribute)
    original[key] = value
    snapshot = getattr(cache.snapshot(), attribute)
    assert snapshot[key] == value
    snapshot[new_key] = {}
    assert new_key not in original


def test_unknown_badge_has_no_icons() -> None:
    """Badge not in badge_set returns minimal dict without icons."""
    snap = BadgeSet(global_badges={}, channel_badges={})
    result = _parse_badge_info("bits", "100", badge_set=snap)

    assert result["name"] == "bits"
    assert "icons" not in result
    assert "title" not in result


def test_empty_badges_string() -> None:
    """Empty badge string returns empty list."""
    snap = BadgeSet(global_badges={}, channel_badges={})
    result = _parse_irc_badges("", "123", badge_set=snap)
    assert result == []

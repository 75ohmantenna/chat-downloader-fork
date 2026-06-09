# SPDX-License-Identifier: MIT

"""Badge-cache purity tests for the Twitch implementation.

These tests verify:

1. Parsing uses the injected ``BadgeSet`` when provided.
2. Message structure produced by parsing is correct (regression coverage).
3. ``BadgeCache`` / ``BadgeSet`` behave correctly (snapshot isolation, etc.).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from chat_downloader.sites.twitch.parsing.badges import (
    _parse_badge_info,
    _parse_irc_badges,
)
from chat_downloader.sites.twitch.parsing.messages import (
    _parse_irc_item,
    _parse_item,
)
from chat_downloader.sites.twitch.types import BadgeCache, BadgeSet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_badge_set(
    global_badge: dict[str, Any] | None = None,
    channel_badge: dict[str, Any] | None = None,
    channel_id: str = "999",
) -> BadgeSet:
    global_badges: dict[Any, Any] = {}
    channel_badges: dict[str, Any] = {}

    if global_badge is not None:
        global_badges[("moderator", "1")] = global_badge

    if channel_badge is not None:
        channel_badges[channel_id] = {("subscriber", "12"): channel_badge}

    return BadgeSet(global_badges=global_badges, channel_badges=channel_badges)


def _known_badge_set() -> BadgeSet:
    return BadgeSet(
        global_badges={
            ("moderator", "1"): {
                "title": "Moderator",
                "image1x": "https://example.com/mod1x.png",
                "image2x": "https://example.com/mod2x.png",
                "image4x": "https://example.com/mod4x.png",
                "clickAction": "visit_url",
                "clickURL": "https://twitch.tv",
            },
        },
        channel_badges={},
    )


# ---------------------------------------------------------------------------
# 1. Parsing uses injected badge_set, not module globals
# ---------------------------------------------------------------------------


def test_parse_badge_info_uses_badge_set_global() -> None:
    """_parse_badge_info uses badge_set.global_badges, ignores module global."""
    badge_set = _make_badge_set(global_badge=_make_badge_dict("CORRECT_GLOBAL"))
    result = _parse_badge_info("moderator", "1", badge_set=badge_set)
    assert result["title"] == "CORRECT_GLOBAL"


def test_parse_badge_info_uses_badge_set_channel() -> None:
    """_parse_badge_info uses badge_set.channel_badges, not module global."""
    badge_set = _make_badge_set(
        channel_badge=_make_badge_dict("CORRECT_CHANNEL"),
        channel_id="999",
    )
    result = _parse_badge_info(
        "subscriber",
        "12",
        channel_id="999",
        badge_set=badge_set,
    )
    assert result["title"] == "CORRECT_CHANNEL"


def test_parse_irc_badges_uses_badge_set() -> None:
    """_parse_irc_badges threads badge_set through to _parse_badge_info."""
    badge_set = _make_badge_set(global_badge=_make_badge_dict("CORRECT_GLOBAL"))
    results = _parse_irc_badges("moderator/1", "999", badge_set=badge_set)
    assert len(results) == 1
    assert results[0]["title"] == "CORRECT_GLOBAL"


def test_parse_irc_item_uses_badge_set() -> None:
    """_parse_irc_item threads badge_set through to badge parsing."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    raw = (
        "@badge-info=subscriber/12;badges=moderator/1,subscriber/12;"
        "color=#FF0000;display-name=TestUser;emotes=;flags=;id=abc123;"
        "mod=1;room-id=999;subscriber=1;tmi-sent-ts=1700000000000;"
        "turbo=0;user-id=12345;user-type=mod "
        ":testuser!testuser@testuser.tmi.twitch.tv PRIVMSG #channel :hello"
        "\r\n"
    )
    match = MESSAGE_REGEX.search(raw)
    assert match is not None, "MESSAGE_REGEX did not match test IRC line"

    badge_set = _make_badge_set(global_badge=_make_badge_dict("CORRECT_GLOBAL"))
    # type: ignore[arg-type]
    result = _parse_irc_item(match, badge_set=badge_set)

    # At least one badge should carry the correct title from badge_set
    badges = result.get("author", {}).get("badges", [])
    titles = [b.get("title") for b in badges if b.get("title")]
    assert "CORRECT_GLOBAL" in titles, (
        f"Expected CORRECT_GLOBAL in titles: {titles}"
    )


def test_parse_item_uses_badge_set() -> None:
    """_parse_item threads badge_set through to badge parsing."""
    badge_set = _make_badge_set(
        channel_badge=_make_badge_dict("CORRECT_CHANNEL"),
        channel_id="999",
    )
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
    result = _parse_item(
        node, offset=0.0, channel_id="999", badge_set=badge_set
    )
    badges = result.get("author", {}).get("badges", [])
    titles = [b.get("title") for b in badges if b.get("title")]
    assert "CORRECT_CHANNEL" in titles, (
        f"Expected CORRECT_CHANNEL in titles: {titles}"
    )


# ---------------------------------------------------------------------------
# 2. Extractor passes badge_cache dicts to update_badge_info
# ---------------------------------------------------------------------------


def test_update_badge_info_called_with_cache_dicts() -> None:
    """_update_badge_info passes badge_cache dict refs to update_badge_info."""
    from chat_downloader.sites.twitch.extractor import TwitchChatDownloader

    downloader = TwitchChatDownloader.__new__(TwitchChatDownloader)
    downloader.badge_cache = BadgeCache()

    with patch(
        "chat_downloader.sites.twitch.extractor.update_badge_info",
    ) as mock_update:
        mock_update.return_value = None
        downloader._update_badge_info("test_channel")

    call_args = mock_update.call_args
    # 4th positional arg = badge_info (global), 5th = subscriber_badge_info
    _, positional = call_args[0][0], call_args[0]
    global_arg = positional[3]
    channel_arg = positional[4]

    assert global_arg is downloader.badge_cache.global_badges, (
        "update_badge_info must receive the cache's global_badges dict"
    )
    assert channel_arg is downloader.badge_cache.channel_badges, (
        "update_badge_info must receive the cache's channel_badges dict"
    )


# ---------------------------------------------------------------------------
# 3. BadgeCache / BadgeSet unit tests
# ---------------------------------------------------------------------------


def test_empty_snapshot() -> None:
    cache = BadgeCache()
    snap = cache.snapshot()
    assert isinstance(snap, BadgeSet)
    assert snap.global_badges == {}
    assert snap.channel_badges == {}


def test_snapshot_is_shallow_copy() -> None:
    """Snapshot() returns independent top-level dicts."""
    cache = BadgeCache()
    cache.global_badges[("mod", "1")] = {"title": "Moderator"}

    snap = cache.snapshot()
    # Mutating the snapshot's top-level dict must not affect the cache
    snap.global_badges[("new", "1")] = {"title": "New"}
    assert ("new", "1") not in cache.global_badges


def test_snapshot_reflects_current_state() -> None:
    cache = BadgeCache()
    cache.global_badges[("mod", "1")] = {"title": "Moderator"}
    snap = cache.snapshot()
    assert snap.global_badges["mod", "1"]["title"] == "Moderator"


def test_in_place_mutation_visible_before_snapshot() -> None:
    """update_badge_info mutates dicts in-place; snapshot reflects it."""
    cache = BadgeCache()
    # Simulate what update_badge_info does:
    cache.global_badges[("mod", "1")] = {"title": "Moderator"}
    snap = cache.snapshot()
    assert ("mod", "1") in snap.global_badges


def test_snapshot_channel_badges_independent() -> None:
    cache = BadgeCache()
    cache.channel_badges["ch1"] = {("sub", "6"): {"title": "6-Month Sub"}}
    snap = cache.snapshot()

    # Adding to snapshot's channel_badges does not affect cache
    snap.channel_badges["ch2"] = {}
    assert "ch2" not in cache.channel_badges


# ---------------------------------------------------------------------------
# 4. Regression: parse_badge_info output structure unchanged
# ---------------------------------------------------------------------------


def test_known_global_badge_has_icons() -> None:
    snap = _known_badge_set()
    result = _parse_badge_info("moderator", "1", badge_set=snap)

    assert result["name"] == "moderator"
    assert "icons" in result
    assert isinstance(result["icons"], list)
    assert len(result["icons"]) > 0
    assert result["title"] == "Moderator"


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

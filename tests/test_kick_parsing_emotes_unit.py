# SPDX-License-Identifier: MIT

from __future__ import annotations

from chat_downloader.sites.kick.parsing.emotes import parse_emotes


def test_no_emotes_returns_text_unchanged() -> None:
    text, emotes = parse_emotes("just plain text")
    assert text == "just plain text"
    assert emotes == []


def test_named_emote_is_substituted_and_structured() -> None:
    text, emotes = parse_emotes("nice [emote:37233:PogU] play")
    assert text == "nice :PogU: play"
    assert len(emotes) == 1
    emote = emotes[0]
    assert emote["id"] == "37233"
    assert emote["name"] == "PogU"
    assert emote["source"] == "kick"
    assert emote["original_marker"] == "[emote:37233:PogU]"
    assert emote["images"][0]["url"] == ("https://files.kick.com/emotes/37233/fullsize")
    # Location points at ":PogU:" in the readable text.
    start, end = emote["locations"][0].split("-")
    assert text[int(start) : int(end) + 1] == ":PogU:"


def test_missing_name_uses_stable_placeholder() -> None:
    text, emotes = parse_emotes("wow [emote:99:]")
    assert text == "wow :emote_99:"
    assert emotes[0]["name"] is None
    start, end = emotes[0]["locations"][0].split("-")
    assert text[int(start) : int(end) + 1] == ":emote_99:"


def test_repeated_emote_merges_locations() -> None:
    text, emotes = parse_emotes("[emote:1:Kappa] x [emote:1:Kappa]")
    assert text == ":Kappa: x :Kappa:"
    assert len(emotes) == 1
    assert len(emotes[0]["locations"]) == 2


def test_repeated_emote_backfills_name_from_later_occurrence() -> None:
    _text, emotes = parse_emotes("[emote:5:] then [emote:5:LaterName]")
    assert len(emotes) == 1
    assert emotes[0]["name"] == "LaterName"
    assert len(emotes[0]["locations"]) == 2

# SPDX-License-Identifier: MIT

"""Twitch emote helpers and message parsing contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.message_emotes import (
    _add_text_for_emotes,
    _generate_emote_image_list,
    _parse_emotes,
)
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item, _parse_item

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "twitch"
_KAPPA_IMAGE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/25/default/light/1.0"


def test_irc_messages_own_emote_images(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _FIXTURE_DIR / "live_events" / "irc-privmsg-with-emotes.json"
    frame = json.loads(fixture.read_text(encoding="utf-8"))["raw"]
    match = MESSAGE_REGEX.search(frame)
    assert match is not None

    images = _parse_irc_item(match)["emotes"][0]["images"]
    assert isinstance(images, tuple)
    assert images[0]["url"] == _KAPPA_IMAGE_URL
    monkeypatch.setitem(images[0], "url", "https://example.invalid/edited.png")

    assert _parse_irc_item(match)["emotes"][0]["images"][0]["url"] == _KAPPA_IMAGE_URL


def test_replay_messages_own_emote_images(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _FIXTURE_DIR / "graphql" / "replay-comment-mobile.json"
    comment = json.loads(fixture.read_text(encoding="utf-8"))["raw"]["node"]

    images = _parse_item(comment, offset=0.0)["emotes"][0]["images"]
    assert isinstance(images, tuple)
    assert images[0]["url"] == _KAPPA_IMAGE_URL
    monkeypatch.setitem(images[0], "url", "https://example.invalid/edited.png")

    next_message = _parse_item(comment, offset=0.0)
    assert next_message["emotes"][0]["images"][0]["url"] == _KAPPA_IMAGE_URL


@pytest.mark.parametrize("emote_id", ["25", "1902"])
def test_emote_images_preserve_size_theme_identity_and_json_shape(emote_id):
    images = _generate_emote_image_list(emote_id)
    expected = [
        {
            "id": image_id,
            "url": f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/{suffix}",
            "width": pixels,
            "height": pixels,
        }
        for image_id, pixels, suffix in (
            ("28x28-light", 28, "light/1.0"),
            ("56x56-light", 56, "light/2.0"),
            ("112x112-light", 112, "light/3.0"),
            ("28x28-dark", 28, "dark/1.0"),
            ("56x56-dark", 56, "dark/2.0"),
            ("112x112-dark", 112, "dark/3.0"),
        )
    ]
    assert isinstance(images, tuple)
    assert list(images) == expected
    assert json.loads(json.dumps(_parse_emotes(f"{emote_id}:0-4"))) == [
        {"id": emote_id, "locations": ["0-4"], "images": expected}
    ]


def test_parse_emotes_empty_string_yields_empty_list() -> None:
    assert _parse_emotes("") == []


def test_parse_emotes_single_emote_single_location() -> None:
    result = _parse_emotes("25:0-4")
    assert len(result) == 1
    assert result[0]["id"] == "25"
    assert result[0]["locations"] == ["0-4"]
    assert len(result[0]["images"]) == 6


def test_parse_emotes_multiple_locations_for_same_emote() -> None:
    result = _parse_emotes("25:0-4,6-10")
    assert len(result) == 1
    assert result[0]["locations"] == ["0-4", "6-10"]


def test_parse_emotes_multiple_distinct_emotes() -> None:
    result = _parse_emotes("25:0-4/1902:6-9")
    assert [(emote["id"], emote["locations"]) for emote in result] == [
        ("25", ["0-4"]),
        ("1902", ["6-9"]),
    ]


def test_add_text_for_emotes_resolves_name_from_message() -> None:
    emotes = [{"locations": ["0-4"]}]
    _add_text_for_emotes("Kappa hello", emotes)
    assert emotes[0]["name"] == "Kappa"


def test_add_text_for_emotes_uses_first_location_to_derive_name() -> None:
    emotes = [{"locations": ["6-10", "0-4"]}]
    _add_text_for_emotes("Kappa Kappa", emotes)
    assert emotes[0]["name"] == "Kappa"


def test_add_text_for_emotes_skips_emote_on_invalid_location(
    monkeypatch,
) -> None:
    debug_calls: list[object] = []
    import chat_downloader.sites.twitch.parsing.message_emotes as mod

    monkeypatch.setattr(mod, "debug_log", lambda *a: debug_calls.append(a))
    emotes = [{"locations": ["bad-loc"]}]
    _add_text_for_emotes("hello world", emotes)
    assert "name" not in emotes[0]
    assert debug_calls  # debug_log was called


def test_add_text_for_emotes_skips_emote_with_missing_locations_key(
    monkeypatch,
) -> None:
    debug_calls: list[object] = []
    import chat_downloader.sites.twitch.parsing.message_emotes as mod

    monkeypatch.setattr(mod, "debug_log", lambda *a: debug_calls.append(a))
    emotes: list[dict[str, object]] = [{}]
    _add_text_for_emotes("hello", emotes)
    assert "name" not in emotes[0]
    assert debug_calls

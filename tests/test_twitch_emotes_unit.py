# SPDX-License-Identifier: MIT

"""Isolated unit tests for message_emotes pure helper functions."""

from __future__ import annotations

import json

import pytest

from chat_downloader.sites.twitch.parsing.message_emotes import (
    _add_text_for_emotes,
    _generate_emote_image_list,
    _parse_emotes,
)


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

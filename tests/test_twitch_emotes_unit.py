# SPDX-License-Identifier: MIT

"""Isolated unit tests for message_emotes pure helper functions."""

import pytest

from chat_downloader.sites.twitch.parsing.message_emotes import (
    _add_text_for_emotes,
    _generate_emote_image_list,
    _parse_emotes,
)


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
    assert len(result) == 2
    ids = {e["id"] for e in result}
    assert ids == {"25", "1902"}


def test_generate_emote_image_list_returns_six_images() -> None:
    images = _generate_emote_image_list("25")
    assert len(images) == 6


@pytest.mark.parametrize(
    "expected_id",
    [
        "28x28-light",
        "56x56-light",
        "112x112-light",
        "28x28-dark",
        "112x112-dark",
    ],
)
def test_generate_emote_image_list_covers_sizes_and_themes(
    expected_id: str,
) -> None:
    images = _generate_emote_image_list("25")
    assert any(img["id"] == expected_id for img in images)


def test_generate_emote_image_list_result_is_cached() -> None:
    first = _generate_emote_image_list("999")
    second = _generate_emote_image_list("999")
    assert first is second


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

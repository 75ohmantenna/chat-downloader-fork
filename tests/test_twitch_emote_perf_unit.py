# SPDX-License-Identifier: MIT

"""Performance and correctness tests for Twitch emote parsing.

Covers:
- _parse_emotes: correct output for representative IRC emote tag strings
- _generate_emote_image_list: caching (same object on repeat calls) + JSON
  serialisation produces an array (not object), and returns 6 images
- _EMOTE_RE: pre-compiled pattern produces same results as re.findall
"""

import json
import re

import pytest

from chat_downloader.sites.twitch.constants import EMOTE_REGEX
from chat_downloader.sites.twitch.parsing.message_emotes import (
    _EMOTE_RE,
    _generate_emote_image_list,
    _parse_emotes,
)

# ---------------------------------------------------------------------------
# Pre-compiled regex
# ---------------------------------------------------------------------------


def test_is_compiled_pattern() -> None:
    assert isinstance(_EMOTE_RE, re.Pattern)


def test_pattern_matches_emote_regex_constant() -> None:
    assert _EMOTE_RE.pattern == EMOTE_REGEX


def test_findall_matches_original_findall() -> None:
    """Pre-compiled findall must equal re.findall with the same pattern."""
    tag = "25:0-4,12-16/1902:6-10"
    expected = re.findall(EMOTE_REGEX, tag)
    actual = _EMOTE_RE.findall(tag)
    assert actual == expected


def test_empty_string() -> None:
    assert _EMOTE_RE.findall("") == []


def test_no_emotes() -> None:
    assert _EMOTE_RE.findall("no-emotes-here") == []


# ---------------------------------------------------------------------------
# _generate_emote_image_list: caching + structure
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_emote_cache() -> None:
    # Clear cache before each test so tests are independent
    _generate_emote_image_list.cache_clear()


def test_returns_six_images() -> None:
    """3 sizes x 2 themes = 6 images per emote."""
    result = _generate_emote_image_list("25")
    assert len(result) == 6


def test_returns_tuple() -> None:
    """Return value must be a tuple (immutable; safe to cache)."""
    result = _generate_emote_image_list("25")
    assert isinstance(result, tuple)


def test_each_image_has_url() -> None:
    result = _generate_emote_image_list("25")
    for img in result:
        assert "url" in img
        assert "25" in img["url"]  # emote_id in URL


def test_urls_cover_both_themes() -> None:
    result = _generate_emote_image_list("25")
    urls = [img["url"] for img in result]
    assert any("light" in u for u in urls)
    assert any("dark" in u for u in urls)


def test_urls_cover_three_sizes() -> None:
    result = _generate_emote_image_list("25")
    urls = [img["url"] for img in result]
    assert any("1.0" in u for u in urls)
    assert any("2.0" in u for u in urls)
    assert any("3.0" in u for u in urls)


def test_cached_returns_same_object() -> None:
    """Second call with same emote_id must return the exact same object."""
    first = _generate_emote_image_list("25")
    second = _generate_emote_image_list("25")
    assert first is second


def test_different_ids_different_objects() -> None:
    a = _generate_emote_image_list("25")
    b = _generate_emote_image_list("1902")
    assert a is not b


def test_cache_info_shows_hits() -> None:
    _generate_emote_image_list("25")
    _generate_emote_image_list("25")  # cache hit
    info = _generate_emote_image_list.cache_info()
    assert info.hits >= 1
    assert info.misses >= 1


def test_json_serialises_as_array() -> None:
    """json.dumps must serialise the returned tuple as a JSON array."""
    result = _generate_emote_image_list("25")
    serialised = json.dumps(result)
    parsed = json.loads(serialised)
    assert isinstance(parsed, list)
    assert len(parsed) == 6


# ---------------------------------------------------------------------------
# _parse_emotes: correctness
# ---------------------------------------------------------------------------


def test_single_emote_single_location() -> None:
    """Basic: one emote, one position range."""
    # Kappa at positions 0-4
    result = _parse_emotes("25:0-4")
    assert len(result) == 1
    emote = result[0]
    assert emote["id"] == "25"
    assert "0-4" in emote["locations"]
    assert isinstance(emote["images"], (list, tuple))
    assert len(emote["images"]) == 6


def test_single_emote_multiple_locations() -> None:
    """One emote appearing at two separate positions."""
    result = _parse_emotes("25:0-4,12-16")
    assert len(result) == 1
    emote = result[0]
    assert "0-4" in emote["locations"]
    assert "12-16" in emote["locations"]


def test_two_different_emotes() -> None:
    """Two distinct emote IDs produce two entries."""
    result = _parse_emotes("25:0-4/1902:6-10")
    assert len(result) == 2
    ids = {e["id"] for e in result}
    assert ids == {"25", "1902"}


def test_empty_string_returns_empty_list() -> None:
    result = _parse_emotes("")
    assert result == []


def test_no_emotes_returns_empty_list() -> None:
    result = _parse_emotes("no-emote-tag-here")
    assert result == []


def test_images_json_serialisable() -> None:
    """Full parse output must serialise to JSON without errors."""
    result = _parse_emotes("25:0-4/1902:6-10")
    json.dumps(result)  # must not raise


def test_images_are_list_or_tuple_of_dicts() -> None:
    result = _parse_emotes("25:0-4")
    images = result[0]["images"]
    assert isinstance(images, (list, tuple))
    for img in images:
        assert isinstance(img, dict)

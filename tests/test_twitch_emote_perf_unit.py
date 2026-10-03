# SPDX-License-Identifier: MIT

"""Compiled IRC emote pattern and cache reuse contracts."""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# _generate_emote_image_list: cache reuse
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_emote_cache() -> None:
    # Clear cache before each test so tests are independent
    _generate_emote_image_list.cache_clear()


def test_cached_returns_same_object() -> None:
    """Second call with same emote_id must return the exact same object."""
    first = _generate_emote_image_list("25")
    second = _generate_emote_image_list("25")
    assert first is second


def test_different_ids_different_objects() -> None:
    a = _generate_emote_image_list("25")
    b = _generate_emote_image_list("1902")
    assert a is not b


# ---------------------------------------------------------------------------
# _parse_emotes: correctness
# ---------------------------------------------------------------------------


def test_no_emotes_returns_empty_list() -> None:
    result = _parse_emotes("no-emote-tag-here")
    assert result == []

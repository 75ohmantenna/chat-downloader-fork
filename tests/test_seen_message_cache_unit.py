# SPDX-License-Identifier: MIT

"""Validation and bookkeeping behavior of _SeenMessageCache."""

from __future__ import annotations

import logging

import pytest

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.twitch import live_service


def test_seen_message_cache_repr_includes_limit_and_size() -> None:
    cache = _SeenMessageCache(limit=4)
    cache.register("a")
    cache.register("b")
    text = repr(cache)
    assert "limit=4" in text
    assert "size=2" in text
    assert "evictions=0" in text


@pytest.mark.parametrize("bad_limit", [-1, -100, None, "nope"])
def test_seen_message_cache_warns_on_invalid_limit(
    bad_limit: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid limits fall back to the default and emit a warning."""
    with caplog.at_level(logging.WARNING):
        cache = _SeenMessageCache(limit=bad_limit)  # type: ignore[arg-type]

    assert cache.limit == DEFAULT_MAX_SEEN_MESSAGE_IDS
    assert any(
        "ignoring invalid limit" in record.message for record in caplog.records
    )


def test_seen_message_cache_zero_limit_silent_fallback_to_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """limit=0 means 'use default' and must not warn but stays bounded."""
    with caplog.at_level(logging.WARNING):
        cache = _SeenMessageCache(limit=0)
    assert cache.limit == DEFAULT_MAX_SEEN_MESSAGE_IDS
    assert not any(
        "ignoring invalid limit" in r.message for r in caplog.records
    )
    # Dedup still works with the default limit.
    assert cache.register("a")[0] is True
    assert cache.register("a")[0] is False


def test_seen_message_cache_accepts_fractional_via_int_cast(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Floats are coerced via int(); 1.5 becomes 1, no warning."""
    with caplog.at_level(logging.WARNING):
        cache = _SeenMessageCache(limit=1.5)  # type: ignore[arg-type]
    assert cache.limit == 1
    assert not any(
        "ignoring invalid limit" in r.message for r in caplog.records
    )


def test_seen_message_cache_evicts_exactly_at_limit() -> None:
    """At limit=2, the third unique register triggers exactly one eviction."""
    cache = _SeenMessageCache(limit=2)
    is_new_a, evicted_a = cache.register("a")
    is_new_b, evicted_b = cache.register("b")
    is_new_c, evicted_c = cache.register("c")

    assert (is_new_a, is_new_b, is_new_c) == (True, True, True)
    assert (evicted_a, evicted_b) == (None, None)
    assert evicted_c == "a"
    assert cache.evictions == 1


def test_twitch_live_service_uses_bumped_cache_limit() -> None:
    """Regression guard so the live default doesn't silently slide back."""
    assert live_service._LIVE_SEEN_MESSAGE_LIMIT == 50_000
    cache = _SeenMessageCache(limit=live_service._LIVE_SEEN_MESSAGE_LIMIT)
    assert cache.limit == 50_000

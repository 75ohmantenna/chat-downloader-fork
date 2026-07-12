# SPDX-License-Identifier: MIT

"""Unit tests for _ContinuationLoop._attempt_profile_fallback."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch


class _FakeDownloader:
    def __init__(
        self, *, auto_fallback: bool = True, profile: str = "youtube_web"
    ) -> None:
        self._auto_profile_fallback = auto_fallback
        self._request_profile = profile
        self.applied_profiles: list[str] = []

    def apply_request_profile(self, name: str) -> bool:
        self.applied_profiles.append(name)
        self._request_profile = name
        return True


def _attempt_fallback(downloader: _FakeDownloader) -> bool:
    """Drive the profile-fallback method on a loop bound to *downloader*."""
    from chat_downloader.sites.youtube.continuation import _ContinuationLoop

    loop = _ContinuationLoop(cast("Any", downloader), {}, {}, cast("Any", None))
    return loop._attempt_profile_fallback()


def test_fallback_disabled_returns_false() -> None:
    """Returns False immediately when _auto_profile_fallback is False."""
    dl = _FakeDownloader(auto_fallback=False)
    assert _attempt_fallback(dl) is False
    assert dl.applied_profiles == []


def test_fallback_applies_next_profile_and_returns_true() -> None:
    """Returns True when a next profile exists and applies successfully."""
    dl = _FakeDownloader(auto_fallback=True, profile="youtube_web")
    with patch(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        return_value="youtube_android",
    ):
        result = _attempt_fallback(dl)
    assert result is True
    assert "youtube_android" in dl.applied_profiles


def test_fallback_returns_false_when_no_next_profile() -> None:
    """When get_next_request_profile returns None the function returns False."""
    dl = _FakeDownloader(auto_fallback=True)
    with patch(
        "chat_downloader.sites.youtube.continuation.get_next_request_profile",
        return_value=None,
    ):
        result = _attempt_fallback(dl)
    assert result is False

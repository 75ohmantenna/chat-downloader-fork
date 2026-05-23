# SPDX-License-Identifier: MIT

"""Unit tests for _attempt_profile_fallback helper."""

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


def _import():
    from chat_downloader.sites.youtube.chat_streams_runtime_iteration import (
        _attempt_profile_fallback,
    )

    return _attempt_profile_fallback


def test_fallback_disabled_returns_false() -> None:
    """When _auto_profile_fallback is False the function returns False
    immediately.
    """
    fn = _import()
    dl = _FakeDownloader(auto_fallback=False)
    assert fn(dl) is False
    assert dl.applied_profiles == []


def test_fallback_applies_next_profile_and_returns_true() -> None:
    """When a next profile exists and applies successfully the function returns
    True.
    """
    fn = _import()
    dl = _FakeDownloader(auto_fallback=True, profile="youtube_web")
    with patch(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.get_next_request_profile",
        return_value="youtube_android",
    ):
        result = fn(dl)
    assert result is True
    assert "youtube_android" in dl.applied_profiles


def test_fallback_returns_false_when_no_next_profile() -> None:
    """When get_next_request_profile returns None the function returns
    False.
    """
    fn = _import()
    dl = _FakeDownloader(auto_fallback=True)
    with patch(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.get_next_request_profile",
        return_value=None,
    ):
        result = fn(dl)
    assert result is False

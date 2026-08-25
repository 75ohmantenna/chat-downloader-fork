# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import VideoUnavailable, VideoUnplayable
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.chat_users_router import (
    YouTubeChatUsersRouterMixin,
)
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader


class _Router(YouTubeChatUsersRouterMixin):
    def __init__(self) -> None:
        self.calls = []

    def _get_chat_by_user_args(self, args, params):
        self.calls.append((args, params))
        return args, params


class _LiveRouter(YouTubeChatUsersRouterMixin):
    _session_get = object()

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def get_chat_by_video_id(self, video_id, params):
        self.calls.append((video_id, params))
        if self.error is not None:
            raise self.error
        return video_id, params


def _live_match(url: str):
    result = YouTubeChatDownloader.matches(url)
    assert result is not None
    function_name, match = result
    assert function_name == "_get_chat_by_live_user"
    return match


def test_live_user_router_resolves_video_before_chat_lookup(monkeypatch) -> None:
    url = "https://www.youtube.com/channel/UCR3TOnFWDeAlT-Ho6LueDmg/live"
    request = ChatRequest(url=url)
    router = _LiveRouter()
    captured = {}

    def fake_get_initial_info(page_url, session_get, params, *patterns):
        captured.update(
            page_url=page_url,
            session_get=session_get,
            params=params,
            pattern_count=len(patterns),
        )
        return {}, {}, {"videoDetails": {"videoId": "LLpNUqHVam8"}}

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_users_router._get_initial_info",
        fake_get_initial_info,
    )

    assert router._get_chat_by_live_user(_live_match(url), request) == (
        "LLpNUqHVam8",
        request,
    )
    assert captured == {
        "page_url": url,
        "session_get": router._session_get,
        "params": request,
        "pattern_count": 3,
    }


@pytest.mark.parametrize(
    "player_response",
    [{}, {"videoDetails": {}}, {"videoDetails": {"videoId": "invalid"}}],
)
def test_live_user_router_rejects_missing_canonical_video(
    monkeypatch,
    player_response,
) -> None:
    url = "https://www.youtube.com/@example/live"
    router = _LiveRouter()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_users_router._get_initial_info",
        lambda *_args: ({}, {}, player_response),
    )

    with pytest.raises(VideoUnavailable, match="Unable to resolve"):
        router._get_chat_by_live_user(_live_match(url), ChatRequest(url=url))

    assert router.calls == []


def test_live_user_router_preserves_resolved_video_error(monkeypatch) -> None:
    url = "https://www.youtube.com/@example/live"
    error = VideoUnplayable("not available in your country")
    router = _LiveRouter(error)
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_users_router._get_initial_info",
        lambda *_args: ({}, {}, {"videoDetails": {"videoId": "LLpNUqHVam8"}}),
    )

    with pytest.raises(VideoUnplayable, match="not available in your country"):
        router._get_chat_by_live_user(_live_match(url), ChatRequest(url=url))


def test_chat_user_router_helper_methods_delegate_expected_keys() -> None:
    router = _Router()
    params = {"url": "https://example.test"}

    assert router.get_chat_by_channel_id("chan", params) == (
        {"channel_id": "chan"},
        params,
    )
    assert router.get_chat_by_user_id("user", params) == (
        {"user_id": "user"},
        params,
    )
    assert router.get_chat_by_custom_username("custom", params) == (
        {"custom_username": "custom"},
        params,
    )
    assert router.get_chat_by_handle("@handle", params) == (
        {"handle": "@handle"},
        params,
    )

    assert router.calls == [
        ({"channel_id": "chan"}, params),
        ({"user_id": "user"}, params),
        ({"custom_username": "custom"}, params),
        ({"handle": "@handle"}, params),
    ]

# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import extractor
from chat_downloader.sites.kick.extractor import KickChatDownloader

ACCEPTED = [
    "https://kick.com/xqc",
    "https://www.kick.com/xqc",
    "http://kick.com/xqc",
    "https://kick.com/some_user-name/",
    "https://kick.com/xqc?clip=123",
    "https://kick.com/xqc#section",
]

REJECTED = [
    "https://kick.com/about",
    "https://kick.com/terms",
    "https://kick.com/privacy",
    "https://kick.com/popout/xqc/chat",
    "https://kick.com/video/123",
    "https://kick.com/xqc/videos/123",
    "https://kick.com/n3on/clips/not-a-clip-id",
    "https://www.youtube.com/watch?v=abc",
    "https://twitch.tv/xqc",
]

CLIP_ID = "clip_01M0BHEHDAX2NEAGXG0DA8V9S5"
CLIP_URL = f"https://kick.com/n3on/clips/{CLIP_ID}"


@pytest.mark.parametrize("url", ACCEPTED)
def test_accepts_channel_urls(url: str) -> None:
    match = KickChatDownloader.matches(url)
    assert match is not None
    handler, regex_match = match
    assert handler == "_get_chat_by_channel"
    assert regex_match.group("id") in {"xqc", "some_user-name"}


@pytest.mark.parametrize("url", REJECTED)
def test_rejects_non_channel_urls(url: str) -> None:
    assert KickChatDownloader.matches(url) is None


@pytest.mark.parametrize(
    "url",
    [
        CLIP_URL,
        f"https://www.kick.com/n3on/clips/{CLIP_ID}/",
        f"https://kick.com/n3on/clips/{CLIP_ID}?autoplay=true",
    ],
)
def test_accepts_clip_urls(url: str) -> None:
    match = KickChatDownloader.matches(url)

    assert match is not None
    handler, regex_match = match
    assert handler == "_get_chat_by_clip"
    assert regex_match.group("id") == "n3on"
    assert regex_match.group("clip_id") == CLIP_ID


def test_site_metadata() -> None:
    assert KickChatDownloader._NAME == "kick.com"
    assert KickChatDownloader._SITE_DEFAULT_PARAMS["format"] == "kick"


def test_downloader_close_releases_both_http_sessions(monkeypatch: Any) -> None:
    kick_client = MagicMock()
    monkeypatch.setattr(extractor, "KickApiClient", lambda **_kwargs: kick_client)
    downloader = KickChatDownloader()
    base_session = downloader.session

    downloader.close()
    downloader.close()

    kick_client.close.assert_called_once()
    assert downloader._session_closed is True
    assert base_session is downloader.session


def test_empty_proxy_disables_environment_for_kick_client(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def build_client(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(extractor, "KickApiClient", build_client)
    downloader = KickChatDownloader(proxy="")
    try:
        assert captured["trust_env"] is False
    finally:
        downloader.close()


def test_client_construction_failure_closes_base_session(monkeypatch: Any) -> None:
    closed: list[Any] = []

    def fail_client(**_kwargs: Any) -> Any:
        raise RuntimeError("client setup failed")

    def track_close(owner: Any) -> None:
        closed.append(owner)

    monkeypatch.setattr(extractor, "KickApiClient", fail_client)
    monkeypatch.setattr(
        extractor.BaseChatDownloader,
        "close",
        track_close,
    )

    with pytest.raises(RuntimeError, match="client setup failed"):
        KickChatDownloader()

    assert len(closed) == 1


def test_closed_downloader_rejects_api_client_access() -> None:
    downloader = KickChatDownloader()
    downloader.close()

    with pytest.raises(RuntimeError, match="closed"):
        _ = downloader._kick_client


def test_get_chat_by_channel_routes_to_builder(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_build(downloader: Any, username: str, request: Any) -> str:
        captured["username"] = username
        captured["request"] = request
        return "CHAT"

    monkeypatch.setattr(extractor, "build_channel_chat", fake_build)
    downloader = KickChatDownloader()
    result = downloader.get_chat_by_channel("xqc", {"url": "https://kick.com/xqc"})
    assert result == "CHAT"
    assert captured["username"] == "xqc"
    assert isinstance(captured["request"], ChatRequest)


def test_internal_router_extracts_username(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_build(downloader: Any, username: str, request: Any) -> str:
        captured["username"] = username
        return "CHAT"

    monkeypatch.setattr(extractor, "build_channel_chat", fake_build)
    downloader = KickChatDownloader()
    match = KickChatDownloader.matches("https://kick.com/somechannel")
    assert match is not None
    downloader._get_chat_by_channel(match[1], {"url": "https://kick.com/somechannel"})
    assert captured["username"] == "somechannel"


def test_get_chat_by_video_passes_owned_client_to_builder(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_build(
        username: str,
        video_id: str,
        request: ChatRequest,
        *,
        api_client: Any,
    ) -> str:
        captured.update(
            username=username,
            video_id=video_id,
            request=request,
            api_client=api_client,
        )
        return "VOD"

    monkeypatch.setattr(extractor, "build_vod_chat", fake_build)
    downloader = KickChatDownloader()

    result = downloader.get_chat_by_video(
        "creator",
        "video-id",
        {"url": "https://kick.com/creator/videos/video-id"},
    )

    assert result == "VOD"
    assert captured["username"] == "creator"
    assert captured["video_id"] == "video-id"
    assert captured["api_client"] is downloader._kick_client


def test_get_chat_by_clip_routes_identifiers_and_owned_client(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_build(
        username: str,
        clip_id: str,
        request: ChatRequest,
        *,
        api_client: Any,
    ) -> str:
        captured.update(
            username=username,
            clip_id=clip_id,
            request=request,
            api_client=api_client,
        )
        return "CLIP"

    monkeypatch.setattr(extractor, "build_clip_chat", fake_build)
    downloader = KickChatDownloader()
    match = KickChatDownloader.matches(CLIP_URL)
    assert match is not None

    result = downloader._get_chat_by_clip(
        match[1],
        {"url": CLIP_URL, "start_time": 5},
    )

    assert result == "CLIP"
    assert captured["username"] == "n3on"
    assert captured["clip_id"] == CLIP_ID
    assert captured["request"].start_time == 5
    assert captured["api_client"] is downloader._kick_client

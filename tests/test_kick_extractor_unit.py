# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any

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
    "https://www.youtube.com/watch?v=abc",
    "https://twitch.tv/xqc",
]


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


def test_site_metadata() -> None:
    assert KickChatDownloader._NAME == "kick.com"
    assert KickChatDownloader._SITE_DEFAULT_PARAMS["format"] == "default"


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

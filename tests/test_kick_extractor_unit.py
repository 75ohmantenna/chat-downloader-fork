# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from requests.cookies import create_cookie

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


@pytest.fixture
def client_factory(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(extractor, "KickApiClient", factory)
    return factory


@pytest.fixture
def downloader(client_factory):
    instance = KickChatDownloader(headers={"X-Trace": "trace-value"})
    yield instance
    instance.close()


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


def test_downloader_close_releases_both_http_sessions(downloader, client_factory):
    base_session = downloader.session
    downloader.close()
    downloader.close()
    client_factory.return_value.close.assert_called_once()
    assert downloader._session_closed is True
    assert base_session is downloader.session


def test_empty_proxy_disables_environment_for_kick_client(client_factory):
    downloader = KickChatDownloader(proxy="")
    try:
        assert client_factory.call_args.kwargs["trust_env"] is False
    finally:
        downloader.close()


@pytest.mark.parametrize(
    ("domain", "value", "path", "expires", "expected"),
    [
        (".kick.com", "encoded%7Ctoken", "/", None, "encoded|token"),
        ("example.com", "token", "/", None, None),
        (".kick.com", "token", "/account", None, None),
        (".kick.com", "token", "/", 1, None),
        (".kick.com", "", "/", None, None),
    ],
)
def test_headers_and_cookie_applicability(
    downloader, client_factory, domain, value, path, expires, expected
):
    downloader.session.cookies.set_cookie(
        create_cookie("session_token", value, domain=domain, path=path, expires=expires)
    )
    configuration = client_factory.call_args.kwargs
    assert configuration["extra_headers"] == {"X-Trace": "trace-value"}
    assert configuration["bearer_token_provider"]() == expected


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


def test_closed_downloader_rejects_api_client_access(downloader) -> None:
    downloader.close()
    with pytest.raises(RuntimeError, match="closed"):
        _ = downloader._kick_client


@pytest.mark.parametrize("internal", [False, True])
def test_channel_routes_to_builder(monkeypatch, downloader, internal):
    builder = MagicMock(return_value="CHAT")
    monkeypatch.setattr(extractor, "build_channel_chat", builder)
    username = "somechannel" if internal else "xqc"
    params = {"url": f"https://kick.com/{username}"}
    if internal:
        match = KickChatDownloader.matches(params["url"])
        assert match is not None
        result = downloader._get_chat_by_channel(match[1], params)
    else:
        result = downloader.get_chat_by_channel(username, params)
    assert result == "CHAT"
    assert builder.call_args.args[1] == username
    assert isinstance(builder.call_args.args[2], ChatRequest)


@pytest.mark.parametrize("kind", ["video", "clip"])
def test_recording_routes_identifiers_and_owned_client(monkeypatch, downloader, kind):
    builder = MagicMock(return_value="CHAT")
    monkeypatch.setattr(
        extractor, "build_vod_chat" if kind == "video" else "build_clip_chat", builder
    )
    if kind == "video":
        username, identifier = "creator", "video-id"
        result = downloader.get_chat_by_video(
            username, identifier, {"url": "https://kick.com/creator/videos/video-id"}
        )
    else:
        username, identifier = "n3on", CLIP_ID
        match = KickChatDownloader.matches(CLIP_URL)
        assert match is not None
        result = downloader._get_chat_by_clip(
            match[1], {"url": CLIP_URL, "start_time": 5}
        )
        assert builder.call_args.args[2].start_time == 5
    assert result == "CHAT"
    assert builder.call_args.args[:2] == (username, identifier)
    assert builder.call_args.kwargs["api_client"] is downloader._kick_client

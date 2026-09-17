# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from requests.cookies import create_cookie

from chat_downloader.sites.kick import extractor
from chat_downloader.sites.kick.extractor import KickChatDownloader

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


@pytest.mark.parametrize(
    ("url", "channel", "clip"),
    [
        *[
            (url, "xqc", False)
            for url in (
                "https://kick.com/xqc",
                "https://www.kick.com/xqc",
                "http://kick.com/xqc",
                "https://kick.com/xqc?clip=123",
                "https://kick.com/xqc#section",
            )
        ],
        ("https://kick.com/some_user-name/", "some_user-name", False),
        *[
            (url, "n3on", True)
            for url in (
                CLIP_URL,
                f"https://www.kick.com/n3on/clips/{CLIP_ID}/",
                f"{CLIP_URL}?autoplay=true",
            )
        ],
        *[
            (url, None, False)
            for url in (
                "https://kick.com/about",
                "https://kick.com/terms",
                "https://kick.com/privacy",
                "https://kick.com/popout/xqc/chat",
                "https://kick.com/video/123",
                "https://kick.com/xqc/videos/123",
                "https://kick.com/n3on/clips/not-a-clip-id",
                "https://www.youtube.com/watch?v=abc",
                "https://twitch.tv/xqc",
            )
        ],
    ],
)
def test_url_matching(url, channel, clip):
    match = KickChatDownloader.matches(url)
    if channel is None:
        assert match is None
    else:
        assert match is not None
        handler, parsed = match
        assert handler == ("_get_chat_by_clip" if clip else "_get_chat_by_channel")
        assert parsed.group("id") == channel
        if clip:
            assert parsed.group("clip_id") == CLIP_ID


def test_close_releases_sessions_and_rejects_client_access(downloader, client_factory):
    base_close = MagicMock(wraps=downloader.session.close)
    downloader.session.close = base_close
    downloader.close()
    downloader.close()
    client_factory.return_value.close.assert_called_once()
    base_close.assert_called_once()
    with pytest.raises(RuntimeError, match="closed"):
        _ = downloader._kick_client


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
    downloader,
    client_factory,
    domain,
    value,
    path,
    expires,
    expected,
):
    downloader.session.cookies.set_cookie(
        create_cookie("session_token", value, domain=domain, path=path, expires=expires)
    )
    configuration = client_factory.call_args.kwargs
    assert configuration["extra_headers"] == {"X-Trace": "trace-value"}
    assert configuration["bearer_token_provider"]() == expected


def test_client_construction_failure_closes_base_session(monkeypatch):
    close = MagicMock()
    monkeypatch.setattr(
        extractor,
        "KickApiClient",
        MagicMock(side_effect=RuntimeError("client setup failed")),
    )
    monkeypatch.setattr(extractor.BaseChatDownloader, "close", close)
    with pytest.raises(RuntimeError, match="client setup failed"):
        KickChatDownloader()
    close.assert_called_once()


@pytest.mark.parametrize("kind", ["channel", "internal", "video", "clip"])
def test_routes_identifiers_request_and_owned_client(monkeypatch, downloader, kind):
    name = "channel" if kind == "internal" else "vod" if kind == "video" else kind
    builder = MagicMock(return_value="CHAT")
    monkeypatch.setattr(extractor, f"build_{name}_chat", builder)
    url = CLIP_URL if kind == "clip" else "https://kick.com/xqc"
    params = {"url": url, "start_time": 5}
    if kind in {"internal", "clip"}:
        match = KickChatDownloader.matches(url)
        method = "_get_chat_by_clip" if kind == "clip" else "_get_chat_by_channel"
        result = getattr(downloader, method)(match[1], params)
    elif kind == "video":
        result = downloader.get_chat_by_video("creator", "video-id", params)
    else:
        result = downloader.get_chat_by_channel("xqc", params)
    assert result == "CHAT"
    args = builder.call_args.args
    if kind in {"internal", "channel"}:
        assert args[1] == "xqc"
    else:
        assert args[:2] == (
            ("n3on", CLIP_ID) if kind == "clip" else ("creator", "video-id")
        )
        assert builder.call_args.kwargs["api_client"] is downloader._kick_client
    assert args[2].start_time == 5

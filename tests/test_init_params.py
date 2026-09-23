# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import InvalidParameter
from chat_downloader.models import DownloaderConfig
from chat_downloader.sites.base import BaseChatDownloader


@pytest.fixture
def cookie_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def environment_proxy(monkeypatch):
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")


@pytest.mark.parametrize(
    ("proxy", "missing_file"),
    [
        ("http://proxy.example.com:8080", True),
        ("http://127.0.0.1.attacker.com:8080", False),
        ("http://127.evil.com:8080", False),
        ("http://0.0.0.0:8080", False),
    ],
)
def test_remote_or_spoofed_loopback_proxy_with_cookies_raises(
    proxy, missing_file, cookie_file
):
    with pytest.raises(InvalidParameter, match="cookie" if missing_file else None):
        ChatDownloader(
            proxy=proxy, cookies="cookies.txt" if missing_file else cookie_file
        )


@pytest.mark.parametrize("proxy", [None, ""])
def test_environment_cookie_proxy_check(environment_proxy, cookie_file, proxy):
    if proxy is None:
        with pytest.raises(InvalidParameter, match="cookie"):
            ChatDownloader(cookies=cookie_file)
    else:
        ChatDownloader(proxy=proxy, cookies=cookie_file).close()


def test_kick_only_environment_proxy_is_checked_for_cookie_auth(
    environment_proxy, cookie_file, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "www.youtube.com,www.twitch.tv")
    with pytest.raises(InvalidParameter, match="cookie"):
        ChatDownloader(cookies=cookie_file)


@pytest.mark.parametrize(
    ("proxy", "cookies"),
    [
        ("http://127.0.0.1:8080", False),
        ("http://127.0.0.2:9999", False),
        ("http://localhost:8888", False),
        ("http://[::1]:8080", False),
        ("http://127.0.0.1:8080", True),
        ("http://localhost:8888", True),
        ("http://127.5.6.7:8080", True),
        ("http://[::1]:8080", True),
    ],
)
def test_loopback_proxy_cookie_warning(proxy, cookies, cookie_file, caplog):
    with caplog.at_level(logging.WARNING):
        session = ChatDownloader(proxy=proxy, cookies=cookie_file if cookies else None)
    session.close()
    assert any("local proxy" in record.message for record in caplog.records) is cookies


def _get_one_message(**init_params) -> None:
    session = ChatDownloader(**init_params)
    chat = None
    try:
        chat = session.get_chat(
            "https://www.youtube.com/watch?v=wXspodtIxYU", max_messages=1
        )
        assert len(list(chat)) == 1
    finally:
        if chat is not None:
            chat.close()
        session.close()


@pytest.mark.network
@pytest.mark.network_environment
@pytest.mark.timeout(90)
@pytest.mark.parametrize(
    ("kind", "platform"),
    [("proxy", None), ("cookies", None)]
    + [
        ("headers", platform)
        for platform in (
            "Windows NT 10.0; Win64; x64",
            "Macintosh; Intel Mac OS X 10_15_7",
            "X11; Linux x86_64",
        )
    ],
)
def test_network_initialization(kind, platform, request) -> None:
    if kind == "proxy":
        for proxy in ("", None, request.getfixturevalue("local_http_proxy")):
            _get_one_message(proxy=proxy)
    elif kind == "cookies":
        _get_one_message(cookies=None)
    else:
        _get_one_message(
            headers={
                "User-Agent": (
                    f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/143.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US, en",
            }
        )


@pytest.fixture
def cookie_session(request):
    session = ChatDownloader(**getattr(request, "param", {}))
    try:
        yield session
    finally:
        session.close()


@pytest.mark.parametrize(
    "options",
    [{}, {"expire_time": 4102444800}, {"path": "/watch"}, {"secure": True}],
    ids=["default", "expiry", "path", "secure"],
)
def test_cookie_operations(cookie_session, options) -> None:
    assert isinstance(cookie_session.config, DownloaderConfig)
    cookie_session.set_cookie_value(
        ".youtube.com", "test_cookie", "test_value", **options
    )
    assert cookie_session.get_cookie_value("test_cookie") == "test_value"
    assert (
        cookie_session.get_cookie_value("nonexistent", default="default") == "default"
    )
    cookie_session.clear_cookies()
    assert cookie_session.get_cookie_value("test_cookie") is None


def test_clear_cookies_disables_future_cookie_file_reloads(cookie_file) -> None:
    class DummySite(BaseChatDownloader):
        _NAME = "dummy"

    seed = DummySite()
    seed.set_cookie_value(".example.com", "sid", "cookie-from-file")
    seed.session.cookies.save(cookie_file, ignore_discard=True, ignore_expires=True)
    seed.close()
    session = ChatDownloader(cookies=cookie_file)
    first_site = session.create_session(DummySite)
    assert first_site.get_cookie_value("sid") == "cookie-from-file"
    session.clear_cookies()
    assert session.config.cookies is None
    first_site.close()
    session.sessions = {}
    second_site = session.create_session(DummySite)
    assert second_site.get_cookie_value("sid") is None
    second_site.close()
    session.close()


@pytest.mark.parametrize(
    "cookie_session",
    [
        {"proxy": "socks5://127.0.0.1:1080"},
        {"headers": {"X-Custom": "val"}, "cookies": None},
        {"proxy": "http://p:8080"},
        {},
    ],
    indirect=True,
)
def test_removed_init_params_property_raises_attribute_error(cookie_session) -> None:
    with pytest.raises(AttributeError):
        _ = cookie_session.init_params

# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import tempfile

# A subset of these tests hit YouTube network APIs via ChatDownloader.get_chat.
import pytest

from chat_downloader import ChatDownloader
from chat_downloader.models import DownloaderConfig
from chat_downloader.sites.base import BaseChatDownloader

YOUTUBE_NETWORK_TEST_URL = "https://www.youtube.com/watch?v=wXspodtIxYU"
_PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
)


def _clear_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _get_one_message(**init_params) -> None:
    session = ChatDownloader(**init_params)
    chat = None
    try:
        chat = session.get_chat(YOUTUBE_NETWORK_TEST_URL, max_messages=1)
        assert len(list(chat)) == 1
    finally:
        if chat is not None:
            chat.close()
        session.close()


def test_proxy_with_cookies_raises() -> None:
    from chat_downloader.errors import InvalidParameter

    with pytest.raises(InvalidParameter, match="cookie"):
        ChatDownloader(proxy="http://proxy.example.com:8080", cookies="cookies.txt")


def test_environment_proxy_with_cookies_raises(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cookie safety applies to the proxy Requests selects from the environment."""
    from chat_downloader.errors import InvalidParameter

    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with pytest.raises(InvalidParameter, match="cookie"):
        ChatDownloader(cookies=str(cookie_file))


def test_empty_proxy_disables_environment_cookie_proxy_check(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    downloader = ChatDownloader(proxy="", cookies=str(cookie_file))
    downloader.close()


@pytest.mark.parametrize(
    "proxy",
    [
        "http://127.0.0.1:8080",
        "http://127.0.0.2:9999",
        "http://localhost:8888",
        "http://[::1]:8080",
    ],
)
def test_loopback_proxy_with_cookies_warns_not_raises(
    proxy: str, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        session = ChatDownloader(proxy=proxy, cookies=None)
    session.close()

    # Loopback with no cookies — no warning expected.
    assert not any("local proxy" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "proxy",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8888",
        "http://127.5.6.7:8080",
        "http://[::1]:8080",
    ],
)
def test_loopback_proxy_with_cookies_emits_warning(
    proxy: str, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        session = ChatDownloader(proxy=proxy, cookies=str(cookie_file))
    session.close()

    assert any("local proxy" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "proxy",
    [
        "http://127.0.0.1.attacker.com:8080",
        "http://127.evil.com:8080",
        "http://0.0.0.0:8080",
    ],
)
def test_spoofed_loopback_proxy_with_cookies_raises(proxy: str, tmp_path) -> None:
    """Hosts that merely look loopback must not enable cookies over a proxy."""
    from chat_downloader.errors import InvalidParameter

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with pytest.raises(InvalidParameter):
        ChatDownloader(proxy=proxy, cookies=str(cookie_file))


@pytest.mark.network
@pytest.mark.network_environment
@pytest.mark.timeout(90)
def test_proxy(local_http_proxy: str) -> None:
    for proxy in ("", None):
        _get_one_message(proxy=proxy)
    _get_one_message(proxy=local_http_proxy)


@pytest.mark.network
@pytest.mark.network_environment
@pytest.mark.timeout(90)
def test_headers() -> None:
    test_user_agents = {
        "windows": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "mac": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "linux": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
    }

    for user_agent in test_user_agents.values():
        test_headers = {
            "User-Agent": user_agent,
            "Accept-Language": "en-US, en",
        }
        _get_one_message(headers=test_headers)


@pytest.mark.network
@pytest.mark.network_environment
@pytest.mark.timeout(90)
def test_cookies() -> None:
    """Test cookie handling."""
    # Test with None cookies (should work)
    _get_one_message(cookies=None)


@pytest.fixture
def cookie_session():
    session = ChatDownloader()
    try:
        yield session
    finally:
        session.close()


def test_cookie_operations(cookie_session) -> None:
    cookie_session.set_cookie_value(".youtube.com", "test_cookie", "test_value")
    assert cookie_session.get_cookie_value("test_cookie") == "test_value"
    assert (
        cookie_session.get_cookie_value("nonexistent", default="default") == "default"
    )
    cookie_session.clear_cookies()
    assert cookie_session.get_cookie_value("test_cookie") is None


def test_clear_cookies_disables_future_cookie_file_reloads() -> None:
    """Clearing cookies stops new site sessions reloading the cookie file."""

    class DummySite(BaseChatDownloader):
        _NAME = "dummy"

    with tempfile.TemporaryDirectory() as temp_dir:
        cookie_path = os.path.join(temp_dir, "cookies.txt")

        seed = DummySite()
        seed.set_cookie_value(".example.com", "sid", "cookie-from-file")
        seed.session.cookies.save(
            cookie_path,
            ignore_discard=True,
            ignore_expires=True,
        )
        seed.close()

        session = ChatDownloader(cookies=cookie_path)
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
    "options",
    [{"expire_time": 4102444800}, {"path": "/watch"}, {"secure": True}],
    ids=["expiry", "path", "secure"],
)
def test_cookie_options(cookie_session, options) -> None:
    cookie_session.set_cookie_value(
        ".youtube.com", "custom_cookie", "custom_value", **options
    )
    assert cookie_session.get_cookie_value("custom_cookie") == "custom_value"


# ── DownloaderConfig integration ──────────────────────────────────────────


def test_config_attribute_is_downloader_config() -> None:
    """ChatDownloader must store a DownloaderConfig, not a raw dict."""
    session = ChatDownloader()
    assert isinstance(session.config, DownloaderConfig)
    session.close()


def test_config_stores_init_kwargs() -> None:
    headers = {"User-Agent": "TestAgent/1.0"}
    session = ChatDownloader(headers=headers, proxy="http://proxy:3128")
    assert session.config.headers == headers
    assert session.config.proxy == "http://proxy:3128"
    assert session.config.cookies is None
    session.close()


@pytest.mark.parametrize(
    "options",
    [
        {"proxy": "socks5://127.0.0.1:1080"},
        {"headers": {"X-Custom": "val"}, "cookies": None},
        {"proxy": "http://p:8080"},
        {},
    ],
)
def test_removed_init_params_property_raises_attribute_error(options) -> None:
    session = ChatDownloader(**options)
    try:
        with pytest.raises(AttributeError):
            _ = session.init_params
    finally:
        session.close()

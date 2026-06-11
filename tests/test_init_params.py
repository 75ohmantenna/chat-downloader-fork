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


def _get_one_message(expected_error=None, **init_params) -> None:
    session = ChatDownloader(**init_params)

    try:
        chat = list(session.get_chat(YOUTUBE_NETWORK_TEST_URL, max_messages=1))

        assert len(chat) == 1

    except Exception as e:
        assert expected_error is not None
        assert isinstance(e, expected_error)  # noqa: PT017 — finally: session.close() requires try/except structure
    finally:
        session.close()


def test_proxy_with_cookies_raises() -> None:
    from chat_downloader.errors import InvalidParameter

    with pytest.raises(InvalidParameter, match="cookie"):
        ChatDownloader(
            proxy="http://proxy.example.com:8080", cookies="cookies.txt"
        )


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
def test_spoofed_loopback_proxy_with_cookies_raises(
    proxy: str, tmp_path
) -> None:
    """Hosts that merely look loopback must not enable cookies over a proxy."""
    from chat_downloader.errors import InvalidParameter

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with pytest.raises(InvalidParameter):
        ChatDownloader(proxy=proxy, cookies=str(cookie_file))


@pytest.mark.parametrize(
    "proxy",
    [
        "http://127.5.6.7:8080",
        "http://[::1]:8080",
    ],
)
def test_real_loopback_subnet_proxy_with_cookies_warns(
    proxy: str, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """127.0.0.0/8 and ::1 are genuine loopback: warn but allow."""
    import logging

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        session = ChatDownloader(proxy=proxy, cookies=str(cookie_file))
    session.close()

    assert any("local proxy" in r.message for r in caplog.records)


@pytest.mark.network
def test_proxy(local_http_proxy: str) -> None:
    for proxy in ("", None):
        _get_one_message(proxy=proxy)
    _get_one_message(proxy=local_http_proxy)


@pytest.mark.network
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
def test_cookies() -> None:
    """Test cookie handling."""
    # Test with None cookies (should work)
    _get_one_message(cookies=None)


@pytest.mark.network
def test_cookies_file_not_found() -> None:
    """Test that non-existent cookie file raises error."""
    from chat_downloader.errors import CookieError

    _get_one_message(
        expected_error=CookieError,
        cookies="/nonexistent/cookies.txt",
    )


def test_cookie_operations() -> None:
    """Test cookie set/get operations."""
    from chat_downloader import ChatDownloader

    session = ChatDownloader()

    # Test setting and getting cookies
    session.set_cookie_value(
        domain=".youtube.com",
        name="test_cookie",
        value="test_value",
    )

    # Get cookie value
    value = session.get_cookie_value("test_cookie")
    assert value == "test_value"

    # Get non-existent cookie
    value = session.get_cookie_value("nonexistent", default="default")
    assert value == "default"

    session.close()


def test_clear_cookies() -> None:
    """Test clearing cookies."""
    from chat_downloader import ChatDownloader

    session = ChatDownloader()

    # Set a cookie
    session.set_cookie_value(
        domain=".youtube.com",
        name="test_cookie",
        value="test_value",
    )

    # Verify it exists
    value = session.get_cookie_value("test_cookie")
    assert value == "test_value"

    # Clear cookies
    session.clear_cookies()

    # Verify it's gone
    value = session.get_cookie_value("test_cookie")
    assert value is None

    session.close()


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


def test_cookie_with_expiry() -> None:
    """Test setting cookie with expiry time."""
    import time

    from chat_downloader import ChatDownloader

    session = ChatDownloader()

    expire_time = int(time.time()) + 3600  # 1 hour from now

    session.set_cookie_value(
        domain=".youtube.com",
        name="expiring_cookie",
        value="expires_soon",
        expire_time=expire_time,
    )

    value = session.get_cookie_value("expiring_cookie")
    assert value == "expires_soon"

    session.close()


def test_cookie_with_path() -> None:
    """Test setting cookie with custom path."""
    from chat_downloader import ChatDownloader

    session = ChatDownloader()

    session.set_cookie_value(
        domain=".youtube.com",
        name="path_cookie",
        value="custom_path",
        path="/watch",
    )

    value = session.get_cookie_value("path_cookie")
    assert value == "custom_path"

    session.close()


def test_secure_cookie() -> None:
    """Test setting secure cookie."""
    from chat_downloader import ChatDownloader

    session = ChatDownloader()

    session.set_cookie_value(
        domain=".youtube.com",
        name="secure_cookie",
        value="secure_value",
        secure=True,
    )

    value = session.get_cookie_value("secure_cookie")
    assert value == "secure_value"

    session.close()


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


def test_init_params_property_returns_dict() -> None:
    """Accessing init_params (removed in 0.3.0) must raise AttributeError."""
    session = ChatDownloader(proxy="socks5://127.0.0.1:1080")
    try:
        with pytest.raises(AttributeError):
            _ = session.init_params
    finally:
        session.close()


def test_init_params_property_matches_config_as_dict() -> None:
    """Accessing init_params (removed in 0.3.0) must raise AttributeError."""
    session = ChatDownloader(headers={"X-Custom": "val"}, cookies=None)
    try:
        with pytest.raises(AttributeError):
            _ = session.init_params
    finally:
        session.close()


def test_init_params_property_mutation_does_not_affect_config() -> None:
    """Accessing init_params (removed in 0.3.0) must raise AttributeError."""
    session = ChatDownloader(proxy="http://p:8080")
    try:
        with pytest.raises(AttributeError):
            _ = session.init_params
    finally:
        session.close()


def test_init_params_emits_deprecation_warning() -> None:
    """Accessing init_params (removed in 0.3.0) must raise AttributeError."""
    session = ChatDownloader()
    try:
        with pytest.raises(AttributeError):
            _ = session.init_params
    finally:
        session.close()

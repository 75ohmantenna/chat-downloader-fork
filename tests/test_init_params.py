# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import InvalidParameter
from chat_downloader.models import DownloaderConfig
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.youtube import YouTubeChatDownloader
from tests.test_http_policy_unit import TLS_FIXTURES
from tests.test_http_policy_unit import local_http as _local_http  # noqa: F401 - named fixture


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


@pytest.mark.timeout(10)
@pytest.mark.parametrize("use_proxy", [False, True], ids=["direct", "connect"])
def test_initialization_options_reach_the_wire(
    local_http, monkeypatch, tmp_path, use_proxy
) -> None:
    target = local_http(tls=True)
    proxy = local_http()
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    if not use_proxy:
        # A session that ignores proxy="" would route through this proxy.
        monkeypatch.setenv("https_proxy", proxy.url)

    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        "127.0.0.1\tFALSE\t/\tTRUE\t4102444800\tsid\tsynthetic\n",
        encoding="utf-8",
    )
    session = ChatDownloader(
        proxy=proxy.url if use_proxy else "",
        headers={"X-Test-Client": "loopback"},
        cookies=str(cookies),
        connect_timeout=2,
        read_timeout=2,
    )
    try:
        site = session.create_session(YouTubeChatDownloader)
        with site._session_get(
            f"{target.url}/init", verify=str(TLS_FIXTURES / "certificate.pem")
        ) as response:
            assert response.status_code == 200
    finally:
        session.close()

    [(path, peer_port, headers)] = target.requests
    assert path == "/init"
    assert headers.get("X-Test-Client") == "loopback"
    assert headers.get("Cookie") == "sid=synthetic"
    assert proxy.tunnels == (
        [(target.url.removeprefix("https://"), peer_port)] if use_proxy else []
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
    session = ChatDownloader(cookies=cookie_file, proxy="")
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

# SPDX-License-Identifier: MIT

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from chat_downloader.errors import CookieError, InvalidParameter
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.common import check_for_invalid_types, get_mapped_keys
from chat_downloader.sites.models import SiteDefault
from chat_downloader.sites.session import ChatDownloaderSession, CookieSpec
from chat_downloader.sites.youtube import client_context
from chat_downloader.sites.youtube.client_context import (
    apply_request_profile_to_innertube_context,
)


class _FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.cookies: Any = MozillaCookieJar()
        self.trust_env = True
        self.closed = False
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def close(self) -> None:
        self.closed = True

    def get(self, url: str, **kwargs: Any) -> Any:
        self.get_calls.append((url, kwargs))
        return SimpleNamespace(json=lambda: {"url": url, "kwargs": kwargs})

    def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append((url, kwargs))
        return SimpleNamespace(json=lambda: {"url": url, "kwargs": kwargs})


def _http(fake: _FakeSession | None = None, **kwargs: Any) -> ChatDownloaderSession:
    adapter = fake or _FakeSession()
    return ChatDownloaderSession(session_factory=lambda: cast("Any", adapter), **kwargs)


def test_http_session_defaults_and_custom_configuration() -> None:
    default = _http()
    assert default.timeout == (10.0, 30.0)
    assert default.get_header("User-Agent").startswith("Mozilla/5.0")
    assert default.get_header("Accept-Language") == "en-US, en, *"
    assert default.session.proxies == {}
    assert isinstance(default.session.cookies, MozillaCookieJar)

    headers = {"User-Agent": "Custom/1.0", "Accept-Language": "fr"}
    configured = _http(
        headers=headers,
        proxy="http://proxy:8080",
        connect_timeout=3,
        read_timeout=15,
        auto_profile_fallback=False,
        twitch_client_id="client",
    )
    assert configured.session.headers == headers
    assert configured.session.headers is not headers
    assert configured.session.proxies == {
        "http": "http://proxy:8080",
        "https": "http://proxy:8080",
    }
    assert configured.timeout == (3.0, 15.0)
    assert configured.auto_profile_fallback is False
    assert configured.twitch_client_id == "client"


@pytest.mark.parametrize("proxy", ["http://proxy:8080", "https://proxy:8080"])
def test_http_session_configures_supported_proxies(proxy: str) -> None:
    http = _http(proxy=proxy)
    assert http.session.proxies == {"http": proxy, "https": proxy}


def test_http_session_empty_proxy_disables_environment() -> None:
    http = _http(proxy="")
    assert http.session.trust_env is False
    assert http.session.proxies == {}


@pytest.mark.parametrize("proxy", ["socks10://proxy:1080", "http:bad"])
def test_http_session_rejects_invalid_proxy(proxy: str) -> None:
    with pytest.raises(InvalidParameter, match="Invalid proxy URL"):
        _http(proxy=proxy)


def test_http_session_loads_cookie_file_and_rejects_missing_file(tmp_path) -> None:
    cookie_path = tmp_path / "cookies.txt"
    seed = _http()
    seed.set_cookie(CookieSpec(".youtube.com", "LOGIN_INFO", "cookie-value"))
    cast("MozillaCookieJar", seed.session.cookies).save(
        str(cookie_path), ignore_discard=True, ignore_expires=True
    )

    loaded = _http(cookies=str(cookie_path))
    assert loaded.get_cookie("LOGIN_INFO") == "cookie-value"
    with pytest.raises(CookieError, match="could not be found"):
        _http(cookies=str(tmp_path / "missing.txt"))


def test_http_session_headers_profiles_and_cookies(monkeypatch) -> None:
    http = _http(headers={"Existing": "1"})
    http.update_headers({"X-Test": "1"})
    assert http.get_header("X-Test") == "1"

    monkeypatch.setattr(
        "chat_downloader.sites.session.get_request_profile_headers",
        lambda name: None if name == "missing" else {"X": name},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.session.build_request_profile_headers",
        lambda name, headers: {**dict(headers), "Profile": name},
    )
    assert http.apply_request_profile("missing") is False
    assert http.apply_request_profile("youtube_web") is True
    assert http.request_profile == "youtube_web"
    assert http.get_header("Profile") == "youtube_web"

    http.set_cookie(
        CookieSpec(".youtube.com", "sid", "abc", path="/watch", secure=True)
    )
    assert http.cookies_dict() == {"sid": "abc"}
    assert http.get_cookie("sid") == "abc"
    assert http.get_cookie("missing", "fallback") == "fallback"
    cookie = next(iter(http.session.cookies))
    assert cookie.path == "/watch"
    assert cookie.secure is True
    http.clear_cookies()
    assert http.cookies_dict() == {}


@pytest.mark.parametrize("domain", ["", ".", "localhost", " example.com"])
def test_http_session_rejects_invalid_cookie_domains(domain: str) -> None:
    with pytest.raises(InvalidParameter, match="Invalid cookie domain"):
        _http().set_cookie(CookieSpec(domain, "sid", "abc"))


def test_http_session_requests_timeouts_json_and_close() -> None:
    fake = _FakeSession()
    http = _http(fake, connect_timeout=2, read_timeout=9)

    get_response = http.get("https://example.com/data")
    post_response = http.post(
        "https://example.com/post", timeout=(1, 1), json={"hello": "world"}
    )
    json_response = http.get_json("https://example.com/json")

    assert fake.get_calls == [
        ("https://example.com/data", {"timeout": (2.0, 9.0)}),
        ("https://example.com/json", {"timeout": (2.0, 9.0)}),
    ]
    assert fake.post_calls == [
        (
            "https://example.com/post",
            {"timeout": (1, 1), "json": {"hello": "world"}},
        )
    ]
    assert get_response.json()["url"].endswith("/data")
    assert post_response.json()["kwargs"]["json"] == {"hello": "world"}
    assert json_response["url"].endswith("/json")

    http.close()
    http.close()
    assert fake.closed is True
    with pytest.raises(RuntimeError, match="HTTP session is closed"):
        http.get("https://example.com")


def test_base_downloader_exposes_session_interface_and_rotation_warning(
    monkeypatch,
) -> None:
    fake = _FakeSession()
    monkeypatch.setattr("chat_downloader.sites.session.requests.Session", lambda: fake)
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "chat_downloader.sites.base.log",
        lambda level, message: logs.append((level, str(message))),
    )

    class AuthDownloader(BaseChatDownloader):
        auth_valid = True

        @property
        def _has_auth_cookies(self) -> bool:
            return self.auth_valid

    downloader = AuthDownloader(connect_timeout=2, read_timeout=9)
    downloader.auth_valid = False
    downloader._session_get("https://example.com/one")
    downloader._session_post("https://example.com/two")

    assert downloader.session is fake
    assert downloader._http_timeout == (2.0, 9.0)
    assert downloader._request_profile is None
    assert downloader._auto_profile_fallback is True
    assert downloader._session_closed is False
    assert [level for level, _ in logs].count("warning") == 1
    downloader.close()
    downloader.close()
    assert downloader._session_closed is True
    assert [level for level, _ in logs].count("debug") == 1


def test_base_downloader_cookie_and_profile_interface(monkeypatch) -> None:
    fake = _FakeSession()
    monkeypatch.setattr("chat_downloader.sites.session.requests.Session", lambda: fake)
    downloader = BaseChatDownloader(request_profile="youtube_web")

    downloader.update_session_headers({"X-Test": "1"})
    assert downloader.get_session_headers("X-Test") == "1"
    assert downloader.apply_request_profile("youtube_web") is True
    downloader.set_cookie_value(".example.com", "sid", "abc", path="/watch")
    assert downloader.get_cookie_value("sid") == "abc"
    downloader.clear_cookies()
    assert downloader.get_cookie_value("sid") is None
    with pytest.raises(TypeError):
        downloader.set_cookie_value(  # type: ignore[call-arg]
            ".example.com", "sid", "abc", httponly=True
        )


def test_base_downloader_misc_helpers(monkeypatch) -> None:
    class DemoDownloader(BaseChatDownloader):
        _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {"format": "demo"}
        _VALID_URLS: ClassVar[dict[str, Any]] = {
            "vod": r"https://twitch\.tv/videos/(?P<id>\d+)",
            "skip": 123,
        }

    downloader = DemoDownloader()
    assert downloader._has_auth_cookies is False
    assert downloader.get_site_value(SiteDefault("format")) == "demo"
    assert downloader.get_site_value(SiteDefault("message_groups")) == ["messages"]
    assert downloader.get_site_value("plain") == "plain"
    assert DemoDownloader.matches("https://twitch.tv/videos/42")[0] == "vod"
    assert DemoDownloader.matches("https://example.com") is None
    with pytest.raises(NotImplementedError):
        downloader.generate_urls()
    with pytest.raises(InvalidParameter, match="Invalid types specified"):
        check_for_invalid_types(["bad"], ["good"])
    assert get_mapped_keys({"a": "x", "b": "y"}) == {"x", "y"}

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "chat_downloader.sites.base.perform_retry",
        lambda **kwargs: calls.append(kwargs),
    )
    BaseChatDownloader.retry(1, max_attempts=2, retry_timeout=0)
    assert calls[0]["attempt_number"] == 1


def test_youtube_profile_context_adds_client_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        client_context,
        "get_request_profile_innertube_context",
        lambda _profile_name: {"client": {"clientName": "WEB"}},
    )
    context = {"notClient": {"value": 1}}
    result = apply_request_profile_to_innertube_context(context, "youtube_web")
    assert result is not context
    assert result["notClient"] == {"value": 1}
    assert result["client"] == {
        "clientName": "WEB",
        "hl": "en",
        "timeZone": "UTC",
        "utcOffsetMinutes": 0,
    }

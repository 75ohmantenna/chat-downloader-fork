# SPDX-License-Identifier: MIT

from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from chat_downloader.errors import CookieError
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.common import check_for_invalid_types, get_mapped_keys
from chat_downloader.sites.models import SiteDefault
from chat_downloader.sites.session import (
    _build_session_headers,
    apply_request_profile,
    check_cookie_rotation,
    clear_cookies,
    close_session,
    get_cookie_value,
    get_cookies_dict,
    get_session_headers,
    init_session_state,
    session_get,
    session_get_json,
    session_post,
    set_cookie_value,
    update_session_headers,
)
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


def _make_owner(*, has_auth_cookies: bool = False) -> Any:
    return SimpleNamespace(_has_auth_cookies=has_auth_cookies)


def test_init_session_state_sets_defaults(monkeypatch) -> None:
    fake_session = _FakeSession()
    owner = _make_owner(has_auth_cookies=True)

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    init_session_state(owner)

    assert owner.session is fake_session
    assert owner._http_timeout == (10.0, 30.0)
    assert owner.session.headers["User-Agent"].startswith("Mozilla/5.0")
    assert owner.session.headers["Accept-Language"] == "en-US, en, *"
    assert owner.session.proxies == {}
    assert isinstance(owner.session.cookies, MozillaCookieJar)
    assert owner._has_initial_auth_cookies is True
    assert owner._cookie_rotation_warned is False


@pytest.mark.parametrize(
    ("proxy", "expected_proxies"),
    [
        (
            "http://proxy:8080",
            {"http": "http://proxy:8080", "https": "http://proxy:8080"},
        ),
        ("", {}),
    ],
)
def test_init_session_state_respects_custom_headers_and_proxy(
    monkeypatch,
    proxy,
    expected_proxies,
) -> None:
    fake_session = _FakeSession()
    owner = _make_owner()
    headers = {"User-Agent": "CustomAgent/1.0", "Accept-Language": "fr-FR"}

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    init_session_state(
        owner,
        headers=headers,
        proxy=proxy,
        connect_timeout=3,
        read_timeout=15,
    )

    assert owner.session.headers == headers
    assert owner.session.headers is not headers
    owner.session.headers["X-Test"] = "1"
    assert "X-Test" not in headers
    assert owner.session.proxies == expected_proxies
    assert owner.session.trust_env is (proxy != "")
    assert owner._http_timeout == (3.0, 15.0)


def test_init_session_state_loads_cookie_file(monkeypatch, tmp_path) -> None:
    cookie_path = tmp_path / "cookies.txt"
    seed_owner = _make_owner()
    init_session_state(
        seed_owner,
        headers={"User-Agent": "SeedAgent", "Accept-Language": "en"},
    )
    set_cookie_value(
        seed_owner,
        domain=".youtube.com",
        name="LOGIN_INFO",
        value="cookie-value",
    )
    cast("MozillaCookieJar", seed_owner.session.cookies).save(
        str(cookie_path),
        ignore_discard=True,
        ignore_expires=True,
    )

    fake_session = _FakeSession()
    owner = _make_owner(has_auth_cookies=False)

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    init_session_state(owner, cookies=str(cookie_path))

    assert get_cookie_value(owner, "LOGIN_INFO") == "cookie-value"


def test_init_session_state_raises_for_missing_cookie_file(
    monkeypatch, tmp_path
) -> None:
    fake_session = _FakeSession()
    owner = _make_owner()

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    with pytest.raises(CookieError, match="could not be found"):
        init_session_state(owner, cookies=str(tmp_path / "missing.txt"))


def test_cookie_rotation_warning_logs_once(monkeypatch) -> None:
    logged: list[tuple[str, str]] = []
    owner = SimpleNamespace(
        _has_initial_auth_cookies=True,
        _has_auth_cookies=False,
        _cookie_rotation_warned=False,
    )

    monkeypatch.setattr(
        "chat_downloader.sites.session.log",
        lambda level, message: logged.append((level, str(message))),
    )

    check_cookie_rotation(owner)
    check_cookie_rotation(owner)

    assert logged == [
        (
            "warning",
            "The provided authentication cookies are no longer valid. "
            "They may have been rotated by your browser as a security measure. "
            "Try exporting fresh cookies from your browser.",
        ),
    ]
    assert owner._cookie_rotation_warned is True


def test_header_cookie_and_close_helpers(monkeypatch) -> None:
    logged: list[tuple[str, str]] = []
    fake_session = _FakeSession()
    owner = _make_owner()

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    init_session_state(
        owner,
        headers={"User-Agent": "CustomAgent/1.0", "Accept-Language": "en"},
    )

    update_session_headers(owner, {"X-Test": "1"})
    set_cookie_value(
        owner,
        domain=".youtube.com",
        name="sid",
        value="abc",
        path="/watch",
        secure=True,
    )

    assert get_session_headers(owner, "X-Test") == "1"
    assert get_cookies_dict(owner) == {"sid": "abc"}
    assert get_cookie_value(owner, "sid") == "abc"
    assert get_cookie_value(owner, "missing", default="fallback") == "fallback"

    cookie = next(iter(owner.session.cookies))
    assert cookie.domain == ".youtube.com"
    assert cookie.path == "/watch"
    assert cookie.secure is True

    clear_cookies(owner)
    assert get_cookies_dict(owner) == {}

    monkeypatch.setattr(
        "chat_downloader.sites.session.log",
        lambda level, message: logged.append((level, str(message))),
    )
    close_session(owner)

    with pytest.raises(RuntimeError, match="HTTP session is closed"):
        session_get(owner, "https://example.com")

    assert owner.session.closed is True
    assert logged == [("debug", "Session closed.")]


def test_session_get_post_and_json_apply_default_timeout_and_rotation(
    monkeypatch,
) -> None:
    logged: list[tuple[str, str]] = []
    fake_session = _FakeSession()
    owner = _make_owner(has_auth_cookies=True)

    monkeypatch.setattr(
        "chat_downloader.sites.session.requests.Session",
        lambda: fake_session,
    )

    init_session_state(
        owner,
        headers={"User-Agent": "CustomAgent/1.0", "Accept-Language": "en"},
        connect_timeout=2,
        read_timeout=9,
    )
    owner._has_initial_auth_cookies = True
    owner._has_auth_cookies = False

    monkeypatch.setattr(
        "chat_downloader.sites.session.log",
        lambda level, message: logged.append((level, str(message))),
    )

    get_response = session_get(owner, "https://example.com/data")
    post_response = session_post(
        owner,
        "https://example.com/post",
        timeout=(1, 1),
        json={"hello": "world"},
    )
    json_response = session_get_json(owner, "https://example.com/json")

    assert owner.session.get_calls[0] == (
        "https://example.com/data",
        {"timeout": (2.0, 9.0)},
    )
    assert owner.session.post_calls[0] == (
        "https://example.com/post",
        {"timeout": (1, 1), "json": {"hello": "world"}},
    )
    assert owner.session.get_calls[1] == (
        "https://example.com/json",
        {"timeout": (2.0, 9.0)},
    )
    assert get_response.json()["url"] == "https://example.com/data"
    assert post_response.json()["kwargs"]["json"] == {"hello": "world"}
    assert json_response == {
        "url": "https://example.com/json",
        "kwargs": {"timeout": (2.0, 9.0)},
    }
    assert len(logged) == 1
    assert logged[0][0] == "warning"


def test_base_downloader_wrapper_methods_delegate(monkeypatch) -> None:
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "chat_downloader.sites.base.check_cookie_rotation",
        lambda owner: calls.append(("check", owner)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.session_header",
        lambda owner, key: ("header", owner, key),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.update_headers",
        lambda owner, headers: calls.append(("update", owner, headers)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.apply_session_request_profile",
        lambda owner, name: ("profile", owner, name),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.clear_session_cookies",
        lambda owner: calls.append(("clear", owner)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.get_cookies_dict",
        lambda owner: {"sid": "1"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.set_session_cookie_value",
        lambda owner, **kwargs: calls.append(("cookie", owner, kwargs)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.get_session_cookie_value",
        lambda owner, name, default=None: (
            "cookie-value",
            owner,
            name,
            default,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.close_session",
        lambda owner: calls.append(("close", owner)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.session_post",
        lambda owner, url, **kwargs: ("post", owner, url, kwargs),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.session_get",
        lambda owner, url, **kwargs: ("get", owner, url, kwargs),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.session_get_json",
        lambda owner, url, **kwargs: ("json", owner, url, kwargs),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.base.perform_retry",
        lambda **kwargs: calls.append(("retry", kwargs)),
    )

    downloader = BaseChatDownloader()

    downloader._check_cookie_rotation()
    assert downloader.get_session_headers("X-Test") == (
        "header",
        downloader,
        "X-Test",
    )
    downloader.update_session_headers({"X-Test": "1"})
    assert downloader.apply_request_profile("youtube_web") == (
        "profile",
        downloader,
        "youtube_web",
    )
    downloader.clear_cookies()
    assert downloader._get_cookies_dict() == {"sid": "1"}
    downloader.set_cookie_value(".example.com", "sid", "abc", path="/watch")
    assert downloader.get_cookie_value("sid", "fallback") == (
        "cookie-value",
        downloader,
        "sid",
        "fallback",
    )
    downloader.close()
    assert downloader._session_post("https://example.com", json={"x": 1}) == (
        "post",
        downloader,
        "https://example.com",
        {"json": {"x": 1}},
    )
    assert downloader._session_get("https://example.com") == (
        "get",
        downloader,
        "https://example.com",
        {},
    )
    assert downloader._session_get_json("https://example.com") == (
        "json",
        downloader,
        "https://example.com",
        {},
    )
    BaseChatDownloader.retry(1, max_attempts=2, retry_timeout=0)

    assert calls[0] == ("check", downloader)
    assert calls[-1][0] == "retry"


def test_base_downloader_misc_helpers() -> None:
    from chat_downloader.errors import InvalidParameter

    class DemoDownloader(BaseChatDownloader):
        _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {"format": "demo"}
        _VALID_URLS: ClassVar[dict[str, Any]] = {
            "vod": r"/videos/(?P<id>\d+)",
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

    assert get_mapped_keys({"a": "x", "b": "y"}) == {
        "x",
        "y",
    }


def test_session_profile_helpers_and_json_builder(monkeypatch) -> None:
    owner = SimpleNamespace(
        session=SimpleNamespace(headers={"Existing": "1"}),
        _request_profile=None,
        _http_timeout=(1.0, 2.0),
        _has_initial_auth_cookies=False,
        _has_auth_cookies=False,
        _cookie_rotation_warned=False,
    )

    monkeypatch.setattr(
        "chat_downloader.sites.session.get_request_profile_headers",
        lambda name: None if name == "missing" else {"X": name},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.session.build_request_profile_headers",
        lambda name, headers: {**dict(headers), "Profile": name},
    )

    assert apply_request_profile(owner, "missing") is False
    assert apply_request_profile(owner, "youtube_web") is True
    assert owner.session.headers == {"Existing": "1", "Profile": "youtube_web"}
    assert owner._request_profile == "youtube_web"

    assert _build_session_headers({}, None)["User-Agent"].startswith("Mozilla/5.0")
    assert _build_session_headers({"User-Agent": "Custom"}, None) == {
        "User-Agent": "Custom",
        "Profile": None,
    }

    monkeypatch.setattr(
        "chat_downloader.sites.session.session_get",
        lambda _owner, url, **kwargs: SimpleNamespace(
            json=lambda: {"url": url, "kwargs": kwargs}
        ),
    )
    assert session_get_json(owner, "https://example.com", timeout=(9, 9)) == {
        "url": "https://example.com",
        "kwargs": {"timeout": (9, 9)},
    }


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


def test_disable_configured_cookie_source_no_config_attr() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.runtime.session_lifecycle import (
        _disable_configured_cookie_source,
    )

    owner = MagicMock(spec=[])  # spec=[] means no attributes defined
    _disable_configured_cookie_source(owner)  # Should return silently


def test_init_session_state_empty_proxy_disables_trust_env() -> None:
    from unittest.mock import MagicMock

    owner = MagicMock()
    owner._has_auth_cookies = False

    init_session_state(owner, proxy="")
    assert owner.session.trust_env is False


def test_init_session_state_http_proxy_sets_proxies() -> None:
    from unittest.mock import MagicMock

    owner = MagicMock()
    owner._has_auth_cookies = False
    url = "http://proxy.example.com:8080"

    init_session_state(owner, proxy=url)

    assert owner.session.proxies.get("http") == url
    assert owner.session.proxies.get("https") == url


def test_init_session_state_https_proxy_sets_proxies() -> None:
    from unittest.mock import MagicMock

    owner = MagicMock()
    owner._has_auth_cookies = False
    url = "https://secure-proxy.example.com:8080"

    init_session_state(owner, proxy=url)

    assert owner.session.proxies.get("http") == url
    assert owner.session.proxies.get("https") == url


@pytest.mark.parametrize("proxy", ["socks10://proxy:1080", "http:bad"])
def test_init_session_state_rejects_invalid_proxy_url(proxy: str) -> None:
    from unittest.mock import MagicMock

    from chat_downloader.errors import InvalidParameter

    owner = MagicMock()
    owner._has_auth_cookies = False

    with pytest.raises(InvalidParameter, match="Invalid proxy URL"):
        init_session_state(owner, proxy=proxy)


# ---------------------------------------------------------------------------
# set_cookie_value — domain validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["", ".", "localhost", " example.com"])
def test_set_cookie_value_rejects_invalid_domain(domain: str) -> None:
    from unittest.mock import MagicMock

    from chat_downloader.errors import InvalidParameter

    owner = MagicMock()
    with pytest.raises(InvalidParameter, match="Invalid cookie domain"):
        set_cookie_value(owner, domain=domain, name="sid", value="abc")


def test_set_cookie_value_accepts_valid_domain() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    jar = MagicMock()
    owner = SimpleNamespace(session=SimpleNamespace(cookies=jar))
    set_cookie_value(owner, domain=".example.com", name="sid", value="abc")
    jar.set_cookie.assert_called_once()


def test_set_cookie_value_rejects_unknown_keyword() -> None:
    from unittest.mock import MagicMock

    owner = MagicMock()
    with pytest.raises(TypeError):
        set_cookie_value(
            owner,
            domain=".example.com",
            name="sid",
            value="abc",
            httponly=True,  # type: ignore[call-arg]
        )

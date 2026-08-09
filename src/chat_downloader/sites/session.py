# SPDX-License-Identifier: MIT

"""Shared HTTP session ownership for site downloaders."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import requests

from chat_downloader._timeout_defaults import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from chat_downloader.errors import CookieError, InvalidParameter
from chat_downloader.request_profiles import (
    build_request_profile_headers,
    get_request_profile_headers,
    normalize_request_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONAny

_ALLOWED_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks5h"})


def _validate_cookie_domain(domain: str) -> None:
    """Reject empty or unscoped cookie domains."""
    normalized_domain = domain.strip()
    if (
        normalized_domain != domain
        or not normalized_domain
        or "." not in normalized_domain.lstrip(".")
    ):
        msg = f"Invalid cookie domain: {domain!r}"
        raise InvalidParameter(msg)


def _validate_proxy_url(proxy: str) -> None:
    allowed = ", ".join(sorted(_ALLOWED_PROXY_SCHEMES))
    msg = f"Invalid proxy URL; expected scheme in {{{allowed}}}"
    try:
        parsed = urlparse(proxy)
    except ValueError:
        raise InvalidParameter(msg) from None
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_PROXY_SCHEMES or not parsed.netloc:
        raise InvalidParameter(msg)


def _build_session_headers(
    provided_headers: dict[str, str] | None,
    request_profile: str | None,
) -> dict[str, str]:
    """Return initial headers after applying request-profile policy."""
    headers = dict(provided_headers) if provided_headers is not None else {}
    if not headers:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US, en, *",
        }
    return build_request_profile_headers(request_profile, headers)


@dataclass(slots=True)
class CookieSpec:
    """Values required to construct one ``http.cookiejar.Cookie``."""

    domain: str
    name: str
    value: str
    expire_time: int | None = None
    port: str | None = None
    path: str = "/"
    secure: bool = False
    discard: bool = False
    rest: dict[str, Any] | None = field(default=None)


def build_cookie(spec: CookieSpec) -> Cookie:
    """Build a validated cookie from ``spec``."""
    _validate_cookie_domain(spec.domain)
    cookie_rest: dict[str, Any] = {} if spec.rest is None else spec.rest
    return Cookie(
        version=0,
        name=spec.name,
        value=spec.value,
        port=spec.port,
        port_specified=spec.port is not None,
        domain=spec.domain,
        domain_specified=True,
        domain_initial_dot=spec.domain.startswith("."),
        path=spec.path,
        path_specified=True,
        secure=spec.secure,
        expires=spec.expire_time,
        discard=spec.discard,
        comment=None,
        comment_url=None,
        rest=cookie_rest,
    )


class ChatDownloaderSession:
    """Own one site's HTTP state behind a cohesive interface."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        cookies: str | None = None,
        proxy: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        request_profile: str | None = None,
        auto_profile_fallback: bool = True,
        twitch_client_id: str | None = None,
        session_factory: Callable[[], requests.Session] | None = None,
    ) -> None:
        """Initialize one requests adapter and all shared HTTP policy state."""
        self.session = (session_factory or requests.Session)()
        self.closed = False
        self.timeout = (float(connect_timeout), float(read_timeout))
        self.request_profile = normalize_request_profile(request_profile)
        self.auto_profile_fallback = bool(auto_profile_fallback)
        self.twitch_client_id = twitch_client_id

        merged_headers = _build_session_headers(headers, self.request_profile)
        self.session.headers.clear()
        self.session.headers.update(merged_headers)

        if proxy is not None:
            if proxy == "":
                self.session.trust_env = False
                proxies: dict[str, str] = {}
            else:
                _validate_proxy_url(proxy)
                proxies = {"http": proxy, "https": proxy}
            self.session.proxies.update(proxies)

        cookie_jar = MozillaCookieJar(cookies) if cookies else MozillaCookieJar()
        if cookies:
            if Path(cookies).exists():
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            else:
                msg = f'The file "{cookies}" could not be found.'
                raise CookieError(msg)
        cast("Any", self.session).cookies = cookie_jar

    def get_header(self, key: str) -> str | None:
        """Return a session header value."""
        return self.session.headers.get(key)

    def update_headers(self, new_headers: dict[str, str]) -> None:
        """Merge headers into the active session."""
        self.session.headers.update(new_headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        """Apply a named request profile; return whether it exists."""
        profile_headers = get_request_profile_headers(profile_name)
        if not profile_headers:
            return False
        session_headers = cast("dict[str, str]", dict(self.session.headers))
        merged_headers = build_request_profile_headers(profile_name, session_headers)
        self.session.headers.clear()
        self.session.headers.update(merged_headers)
        self.request_profile = profile_name
        return True

    def clear_cookies(self) -> None:
        """Clear the session cookie jar."""
        self.session.cookies.clear()

    def cookies_dict(self) -> dict[str, str]:
        """Return the current cookie jar as a plain dictionary."""
        return {
            cookie.name: cookie.value
            for cookie in self.session.cookies
            if cookie.value is not None
        }

    def set_cookie(self, spec: CookieSpec) -> None:
        """Add a validated cookie to the session."""
        self.session.cookies.set_cookie(build_cookie(spec))

    def get_cookie(self, name: str, default: str | None = None) -> str | None:
        """Return a cookie value if present."""
        return self.cookies_dict().get(name, default)

    def _require_open(self) -> None:
        if self.closed:
            msg = "HTTP session is closed; create a new downloader session."
            raise RuntimeError(msg)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        """POST with the configured default timeout."""
        self._require_open()
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """GET with the configured default timeout."""
        self._require_open()
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> JSONAny:
        """GET and parse a JSON response."""
        return cast("JSONAny", self.get(url, **kwargs).json())

    def close(self) -> None:
        """Close the underlying session once."""
        if self.closed:
            return
        try:
            self.session.close()
        finally:
            self.closed = True

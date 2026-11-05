# SPDX-License-Identifier: MIT

"""Shared HTTP session ownership for site downloaders."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin, urlparse, urlsplit

import requests

from chat_downloader._timeout_defaults import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from chat_downloader.debugging import log
from chat_downloader.errors import CookieError, InvalidParameter, ParsingError
from chat_downloader.request_profiles import (
    get_request_profile_headers,
    normalize_request_profile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from http.cookiejar import CookieJar
    from typing import Protocol

    from chat_downloader.utils.json_types import JSONAny

    class _StatusResponse(Protocol):
        @property
        def status_code(self) -> int: ...


_ALLOWED_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks5h"})
_DEFAULT_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US, en, *",
}


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
    headers = dict(_DEFAULT_SESSION_HEADERS)
    headers.update(get_request_profile_headers(request_profile))
    if provided_headers:
        headers.update(provided_headers)
    return headers


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


def get_scoped_cookie(
    cookies: CookieJar,
    name: str,
    url: str,
    allowed_domains: frozenset[str],
) -> Cookie | None:
    """Select an applicable provider cookie, failing closed on ambiguity."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    matches = [
        cookie
        for cookie in cookies
        if cookie.name == name
        and cookie.domain.lstrip(".").lower() in allowed_domains
        and _cookie_applies(cookie, host, path, parsed.scheme, parsed.port)
    ]
    values = {cookie.value for cookie in matches}
    if len(values) != 1:
        return None
    # Equal values need no jar-order tie breaker. Prefer the narrowest scope so
    # derived cookies never broaden an otherwise identical credential.
    return min(
        matches,
        key=lambda cookie: (
            -len(cookie.path),
            -len(cookie.domain),
            cookie.domain,
            cookie.expires if cookie.expires is not None else float("inf"),
            not cookie.secure,
            cookie.port or "",
        ),
    )


def _cookie_applies(
    cookie: Cookie, host: str, path: str, scheme: str, port: int | None
) -> bool:
    """Check domain, path, lifetime, transport and explicit port restrictions."""
    domain = cookie.domain.lstrip(".").lower()
    domain_matches = host == domain or (
        cookie.domain_specified and host.endswith("." + domain)
    )
    cookie_path = cookie.path or "/"
    path_matches = path == cookie_path or path.startswith(
        cookie_path if cookie_path.endswith("/") else cookie_path + "/"
    )
    actual_port = str(port or (443 if scheme == "https" else 80))
    return (
        domain_matches
        and path_matches
        and not cookie.is_expired()
        and (not cookie.secure or scheme == "https")
        and (not cookie.port_specified or actual_port in (cookie.port or "").split(","))
    )


def _https_origin(url: str) -> tuple[str, int]:
    """Require a credential-free HTTPS URL before a redirect request."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return parsed.hostname, parsed.port or 443
    except ValueError:
        pass
    msg = "Unsafe HTTP redirect destination."
    raise ParsingError(msg)


def reject_redirect(response: _StatusResponse) -> None:
    """Reject redirects before interpreting provider-controlled response bodies."""
    if 300 <= response.status_code < 400:
        msg = "Unexpected HTTP redirect from provider."
        raise ParsingError(msg)


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
        self._explicit_headers = dict(headers or {})
        self._explicit_header_names = {key.casefold() for key in headers or ()}

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

    def replace_headers(
        self,
        new_headers: dict[str, str],
        managed_names: Iterable[str],
    ) -> None:
        """Replace generated headers while preserving explicit user values."""
        managed = {name.casefold() for name in managed_names}
        for name in tuple(self.session.headers):
            if name.casefold() in managed:
                del self.session.headers[name]
        self.session.headers.update(new_headers)
        # Generated headers may include profile fields outside the managed API
        # set (notably User-Agent). They must not replace explicit overrides.
        restore = managed | {name.casefold() for name in new_headers}
        for name, value in self._explicit_headers.items():
            if name.casefold() in restore:
                for current_name in tuple(self.session.headers):
                    if current_name.casefold() == name.casefold():
                        del self.session.headers[current_name]
                self.session.headers[name] = value

    def apply_request_profile(self, profile_name: str) -> bool:
        """Apply a named request profile; return whether it exists."""
        profile_headers = get_request_profile_headers(profile_name)
        if not profile_headers:
            return False
        for name, value in profile_headers.items():
            if name.casefold() not in self._explicit_header_names:
                self.session.headers[name] = value
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
        kwargs["allow_redirects"] = False
        started = perf_counter()
        try:
            try:
                response = self.session.post(url, **kwargs)
            except ValueError:
                msg = "Invalid provider request or redirect URL."
                raise ParsingError(msg) from None
            reject_redirect(response)
            return response
        finally:
            log("debug", f"HTTP POST {url} took {perf_counter() - started:.3f}s")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """GET with the configured default timeout."""
        self._require_open()
        kwargs.setdefault("timeout", self.timeout)
        follow_redirects = kwargs.pop("allow_redirects", False)
        kwargs["allow_redirects"] = False
        origin = _https_origin(url) if follow_redirects else None
        started = perf_counter()
        try:
            for _ in range(6):
                try:
                    response = self.session.get(url, **kwargs)
                except ValueError:
                    msg = "Invalid provider request or redirect URL."
                    raise ParsingError(msg) from None
                if not 300 <= response.status_code < 400:
                    return response
                if not follow_redirects:
                    reject_redirect(response)
                try:
                    destination = urljoin(url, response.headers.get("Location", ""))
                except ValueError:
                    msg = "Unsafe HTTP redirect destination."
                    raise ParsingError(msg) from None
                if not response.headers.get("Location"):
                    reject_redirect(response)
                if _https_origin(destination) != origin:
                    msg = "Cross-origin HTTP redirect from provider."
                    raise ParsingError(msg)
                response.close()
                url = destination
                # Query parameters belong only to the first request.
                kwargs.pop("params", None)
            msg = "Provider HTTP redirect limit exceeded."
            raise ParsingError(msg)
        finally:
            log("debug", f"HTTP GET {url} took {perf_counter() - started:.3f}s")

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

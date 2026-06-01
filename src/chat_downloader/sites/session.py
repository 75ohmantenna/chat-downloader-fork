# SPDX-License-Identifier: MIT

"""Shared HTTP session and cookie helpers for site downloaders."""

import os
from dataclasses import dataclass, field
from http.cookiejar import Cookie, MozillaCookieJar
from typing import Any, cast
from urllib.parse import urlparse

import requests

from chat_downloader._timeout_defaults import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from chat_downloader.debugging import log
from chat_downloader.errors import CookieError, InvalidParameter
from chat_downloader.request_profiles import (
    build_request_profile_headers,
    get_request_profile_headers,
    normalize_request_profile,
)

from ._protocols import SessionOwnerProto

_ALLOWED_PROXY_SCHEMES = frozenset(
    {"http", "https", "socks4", "socks5", "socks5h"}
)


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


def init_session_state(owner: SessionOwnerProto, **kwargs: Any) -> None:
    """Initialize HTTP session, headers, proxies, cookies, and timeout state."""
    owner.session = requests.Session()

    connect_timeout = float(
        kwargs.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    )
    read_timeout = float(kwargs.get("read_timeout", DEFAULT_READ_TIMEOUT))
    owner._http_timeout = (connect_timeout, read_timeout)

    provided_headers = kwargs.get("headers")
    request_profile = normalize_request_profile(kwargs.get("request_profile"))
    merged_headers = _build_session_headers(provided_headers, request_profile)
    owner.session.headers.clear()
    owner.session.headers.update(merged_headers)
    owner._request_profile = request_profile
    owner._auto_profile_fallback = bool(
        kwargs.get("auto_profile_fallback", True)
    )
    owner._twitch_client_id = kwargs.get("twitch_client_id")

    proxy = kwargs.get("proxy")
    if proxy is not None:
        if proxy == "":
            owner.session.trust_env = False
            proxies = {}
        else:
            _validate_proxy_url(proxy)
            proxies = {"http": proxy, "https": proxy}
        owner.session.proxies.update(proxies)

    cookies = kwargs.get("cookies")
    cookie_jar = MozillaCookieJar(cookies) if cookies else MozillaCookieJar()

    if cookies:
        if os.path.exists(cookies):
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
        else:
            msg = f'The file "{cookies}" could not be found.'
            raise CookieError(msg)

    cast(Any, owner.session).cookies = cookie_jar
    owner._has_initial_auth_cookies = owner._has_auth_cookies
    owner._cookie_rotation_warned = False


def _validate_proxy_url(proxy: str) -> None:
    parsed = urlparse(proxy)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_PROXY_SCHEMES or not parsed.netloc:
        allowed = ", ".join(sorted(_ALLOWED_PROXY_SCHEMES))
        msg = f"Invalid proxy URL {proxy!r}; expected scheme in {{{allowed}}}"
        raise InvalidParameter(msg)


def check_cookie_rotation(owner: SessionOwnerProto) -> None:
    """Warn once if auth cookies present at startup have been rotated away."""
    if (
        owner._has_initial_auth_cookies
        and not owner._has_auth_cookies
        and not owner._cookie_rotation_warned
    ):
        log(
            "warning",
            "The provided authentication cookies are no longer valid. "
            "They may have been rotated by your browser as a security measure. "
            "Try exporting fresh cookies from your browser.",
        )
        owner._cookie_rotation_warned = True


def get_session_headers(owner: SessionOwnerProto, key: str) -> Any:
    """Return a session header value."""
    return owner.session.headers.get(key)


def update_session_headers(
    owner: SessionOwnerProto, new_headers: dict[str, str]
) -> None:
    """Update session headers in place."""
    owner.session.headers.update(new_headers)


def apply_request_profile(owner: SessionOwnerProto, profile_name: str) -> bool:
    """Apply a request profile to session headers; return True on success."""
    profile_headers = get_request_profile_headers(profile_name)
    if not profile_headers:
        return False
    session_headers = cast("dict[str, str]", dict(owner.session.headers))
    merged_headers = build_request_profile_headers(
        profile_name, session_headers
    )
    owner.session.headers.clear()
    owner.session.headers.update(merged_headers)
    owner._request_profile = profile_name
    return True


def _build_session_headers(
    provided_headers: dict[str, str] | None,
    request_profile: str | None,
) -> dict[str, str]:
    """Return the initial session headers after applying profile policy."""
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


def clear_cookies(owner: SessionOwnerProto) -> None:
    """Clear the session cookie jar."""
    owner.session.cookies.clear()


def get_cookies_dict(owner: SessionOwnerProto) -> dict[str, str]:
    """Return the current cookie jar as a plain dictionary."""
    return {
        cookie.name: cookie.value
        for cookie in owner.session.cookies
        if cookie.value is not None
    }


@dataclass(slots=True)
class CookieSpec:
    """All fields required to construct a single ``http.cookiejar.Cookie``.

    This dataclass gathers the ten cookie parameters that appear in every
    ``set_cookie_value`` call-site into one named object.  It is intentionally
    internal to the session layer; external callers should use
    :meth:`BaseChatDownloader.set_cookie_value` or
    :meth:`ChatDownloader.set_cookie_value` instead.

    Attributes:
        domain: Cookie domain (e.g. ``".twitch.tv"``).
        name: Cookie name.
        value: Cookie value string.
        expire_time: Unix timestamp for expiry, or ``None`` for session
            lifetime.
        port: Port restriction string (e.g. ``"443"``), or ``None``.
        path: URL path scope; defaults to ``"/"``.
        secure: Whether the cookie must only be sent over HTTPS.
        discard: Whether this is a session (non-persistent) cookie.
        rest: Additional unrecognised cookie attributes dict, or ``None``.
    """

    domain: str
    name: str
    value: str
    expire_time: int | None = None
    port: str | None = None
    path: str = "/"
    secure: bool = False
    discard: bool = False
    rest: dict[str, Any] | None = field(default=None)


def _set_cookie_from_spec(owner: SessionOwnerProto, spec: CookieSpec) -> None:
    """Add a cookie described by *spec* to *owner*'s session cookie jar.

    Args:
        owner: Any object that holds a ``session`` with a ``cookies``
            ``CookieJar``.
        spec: The fully-specified cookie to add.
    """
    cookie_rest: dict[str, Any] = {} if spec.rest is None else spec.rest
    cookie = Cookie(
        0,
        spec.name,
        spec.value,
        spec.port,
        spec.port is not None,
        spec.domain,
        True,
        spec.domain.startswith("."),
        spec.path,
        True,
        spec.secure,
        spec.expire_time,
        spec.discard,
        None,
        None,
        cookie_rest,
    )
    cast(Any, owner.session.cookies).set_cookie(cookie)


def set_cookie_value(
    owner: SessionOwnerProto,
    domain: str,
    name: str,
    value: str,
    expire_time: int | None = None,
    port: str | None = None,
    path: str = "/",
    secure: bool = False,
    discard: bool = False,
    rest: dict[str, Any] | None = None,
) -> None:
    """Set a cookie value on the session cookie jar."""
    _validate_cookie_domain(domain)
    _set_cookie_from_spec(
        owner,
        CookieSpec(
            domain=domain,
            name=name,
            value=value,
            expire_time=expire_time,
            port=port,
            path=path,
            secure=secure,
            discard=discard,
            rest=rest,
        ),
    )


def get_cookie_value(
    owner: SessionOwnerProto, name: str, default: Any = None
) -> Any:
    """Return a cookie value if present, otherwise default."""
    return get_cookies_dict(owner).get(name, default)


def close_session(owner: SessionOwnerProto) -> None:
    """Close the underlying requests session."""
    owner.session.close()
    log("debug", "Session closed.")


def session_post(owner: SessionOwnerProto, url: str, **kwargs: Any) -> Any:
    """Make a POST request using the configured session."""
    kwargs.setdefault("timeout", owner._http_timeout)
    response = owner.session.post(url, **kwargs)
    check_cookie_rotation(owner)
    return response


def session_get(owner: SessionOwnerProto, url: str, **kwargs: Any) -> Any:
    """Make a GET request using the configured session."""
    kwargs.setdefault("timeout", owner._http_timeout)
    response = owner.session.get(url, **kwargs)
    check_cookie_rotation(owner)
    return response


def session_get_json(owner: SessionOwnerProto, url: str, **kwargs: Any) -> Any:
    """Make a GET request and parse the response as JSON."""
    return session_get(owner, url, **kwargs).json()

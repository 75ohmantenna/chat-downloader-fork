# SPDX-License-Identifier: MIT

"""Kick HTTP-session construction with anti-challenge fallbacks."""

from __future__ import annotations

from typing import Any, Protocol, cast

import requests

from chat_downloader.debugging import logger


class _KickSession(Protocol):
    """Minimal session interface owned by :class:`KickApiClient`."""

    def get(self, url: str, **kwargs: object) -> requests.Response: ...

    def close(self) -> None: ...


def create_kick_session(
    *,
    proxy: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> _KickSession:  # pragma: no cover — live optional-dependency path
    """Create a dedicated Kick API session with challenge-aware fallbacks."""
    session = _try_curl_cffi()
    if session is None:
        session = _try_cloudscraper()
    if session is None:
        session = _make_plain_session()
    if proxy:
        session.proxies.update(proxy)
    if extra_headers:
        session.headers.update(extra_headers)
    return cast("_KickSession", session)


def _try_curl_cffi() -> Any | None:  # pragma: no cover
    """Try a curl-cffi session with a Chrome TLS fingerprint."""
    try:
        from curl_cffi import requests as curl_requests

        session: Any = curl_requests.Session()
        session.impersonate = "chrome124"
        session.headers.update(_browser_headers("124"))
    except ImportError:
        logger.debug("curl-cffi unavailable; skipping impersonated Kick session.")
        return None
    else:
        return session


def _try_cloudscraper() -> Any | None:  # pragma: no cover
    """Try a cloudscraper session for simpler JavaScript challenges."""
    try:
        import cloudscraper  # type: ignore[import-untyped]

        session = cloudscraper.create_scraper()
        session.headers.update(_browser_headers("120"))
    except ImportError:
        logger.debug("cloudscraper unavailable; skipping Kick scraper session.")
        return None
    else:
        return session


def _make_plain_session() -> requests.Session:  # pragma: no cover
    """Create the final plain-requests fallback."""
    session = requests.Session()
    session.headers.update(_browser_headers("120"))
    return session


def _browser_headers(chrome_version: str) -> dict[str, str]:
    """Return the provider's browser-like default request headers."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version}.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://kick.com/",
        "DNT": "1",
    }

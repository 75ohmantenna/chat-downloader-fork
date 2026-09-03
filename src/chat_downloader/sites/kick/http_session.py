# SPDX-License-Identifier: MIT

"""Kick HTTP-session backend selection."""

from __future__ import annotations

from typing import Any, Protocol, cast

import requests

from chat_downloader.debugging import logger


class _KickSession(Protocol):
    """Minimal session interface owned by :class:`KickApiClient`."""

    trust_env: bool

    def get(self, url: str, **kwargs: object) -> requests.Response: ...

    def close(self) -> None: ...


def create_kick_session(
    *,
    proxy: dict[str, str] | None = None,
    extra_headers: dict[str, str] | None = None,
    trust_env: bool = True,
) -> _KickSession:  # pragma: no cover — live optional-dependency path
    """Create a dedicated Kick API session with the first available backend."""
    session = _try_curl_cffi()
    if session is None:
        session = _try_cloudscraper()
    if session is None:
        session = _make_plain_session()
    session.trust_env = trust_env
    if proxy:
        session.proxies.update(proxy)
    if extra_headers:
        session.headers.update(extra_headers)
    return cast("_KickSession", session)


def _try_curl_cffi() -> Any | None:  # pragma: no cover
    """Try a curl-cffi session with its current Chrome TLS fingerprint."""
    try:
        from curl_cffi import requests as curl_requests

        session: Any = curl_requests.Session(impersonate="chrome")
        session.headers.update(_api_headers())
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
        session.headers.update(_api_headers())
    except ImportError:
        logger.debug("cloudscraper unavailable; skipping Kick scraper session.")
        return None
    else:
        return session


def _make_plain_session() -> requests.Session:  # pragma: no cover
    """Create the final plain-requests fallback."""
    session = requests.Session()
    session.headers.update(_plain_browser_headers())
    return session


def _api_headers() -> dict[str, str]:
    """Return provider-specific headers without overriding backend identity."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://kick.com/",
        "DNT": "1",
    }


def _plain_browser_headers() -> dict[str, str]:
    """Return browser-like headers for the non-impersonating fallback."""
    return {
        **_api_headers(),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

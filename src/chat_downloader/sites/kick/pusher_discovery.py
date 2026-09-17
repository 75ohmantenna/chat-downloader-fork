# SPDX-License-Identifier: MIT

"""Select and refresh Kick's public Pusher key for anonymous read-only chat.

Normal connections use a compiled-in key. Explicit refresh scans homepage-linked
Next.js chunks for ``NEXT_PUBLIC_PUSHER_KEY``, caches it for the process lifetime,
and falls back to the default on failure. Network I/O stays out of constants;
an injectable HTTP client enables offline tests.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import Protocol, cast

import requests

#: Default public Pusher application key used by Kick chat.
#: This is not a secret; it grants only anonymous, read-only subscription to
#: public chatroom channels.
_PUSHER_DEFAULT_KEY = "32cbd69e4b950bf97679"
_DISCOVERY_REQUEST_TIMEOUT_SECONDS = 3.0
_DISCOVERY_TOTAL_TIMEOUT_SECONDS = 10.0

#: Pusher WebSocket URL template, formatted with the resolved app key.
_PUSHER_WS_TEMPLATE = (
    "wss://ws-us2.pusher.com/app/{key}?protocol=7&client=js&version=7.6.0&flash=false"
)


class PusherKeyCache:
    """Injectable discovered-key cache; ``key`` is ``None`` until resolved.

    Tests can inject isolated instances instead of mutating shared module state.
    """

    def __init__(self) -> None:
        """Initialize an empty cache with no resolved key."""
        self.key: str | None = None


#: Process-wide cache used by :func:`resolve_pusher_key` when no cache is passed.
_pusher_key_cache = PusherKeyCache()


class _HttpResponse(Protocol):
    """Minimal response shape required by Pusher-key discovery."""

    status_code: int
    text: str


class _HttpClient(Protocol):
    """Minimal HTTP client shape required by Pusher-key discovery."""

    def get(
        self, url: str, *, timeout: float, allow_redirects: bool = False
    ) -> _HttpResponse: ...
    def close(self) -> None: ...


class _RequestsHttpClient:
    """Thin ``requests`` adapter used by default discovery."""

    def __init__(
        self,
        session: requests.Session | None = None,
        configured_timeout: tuple[float, float] | None = None,
    ) -> None:
        self._owns_session = session is None
        self._session = session or requests.Session()
        self._configured_timeout = configured_timeout
        if self._owns_session:
            self._session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html",
                }
            )

    def get(
        self, url: str, *, timeout: float, allow_redirects: bool = False
    ) -> _HttpResponse:
        effective_timeout: float | tuple[float, float] = timeout
        if self._configured_timeout is not None:
            effective_timeout = (
                min(self._configured_timeout[0], timeout),
                min(self._configured_timeout[1], timeout),
            )
        response = self._session.get(
            url,
            timeout=effective_timeout,
            allow_redirects=allow_redirects,
        )
        return cast("_HttpResponse", cast("object", response))

    def close(self) -> None:
        if self._owns_session:
            with contextlib.suppress(OSError, RuntimeError):
                self._session.close()


def _is_kick_origin(url: str) -> bool:
    """Restrict discovery scripts to Kick HTTPS origins to prevent arbitrary fetches."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "kick.com" or host.endswith(".kick.com")


def _discover_pusher_key(http_client: _HttpClient) -> str | None:
    """Scan Kick's homepage and JS bundles for a Pusher key.

    Returns ``None`` when no key can be found.
    """
    deadline = time.monotonic() + _DISCOVERY_TOTAL_TIMEOUT_SECONDS
    try:
        homepage = http_client.get(
            "https://kick.com/",
            timeout=_DISCOVERY_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except OSError:
        return None
    if not 200 <= homepage.status_code < 300:
        return None

    # Find all JS chunk URLs in the page (scan at most 15 chunks)
    script_urls = re.findall(r'<script[^>]*src="([^"]+\.js)"[^>]*>', homepage.text)
    for url in script_urls[:15]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        abs_url = url if url.startswith("http") else "https://kick.com" + url
        # Only fetch scripts served over HTTPS from Kick's own domain.
        # Without this guard a tampered/MITM'd homepage could point the
        # loader at an arbitrary host (SSRF, e.g. cloud metadata endpoints).
        # ``*.kick.com`` is allowed so CDN-hosted bundles still resolve.
        if not _is_kick_origin(abs_url):
            continue
        try:
            js_resp = http_client.get(
                abs_url,
                timeout=min(_DISCOVERY_REQUEST_TIMEOUT_SECONDS, remaining),
                allow_redirects=False,
            )
        except OSError:
            continue
        if not 200 <= js_resp.status_code < 300:
            continue
        try:
            match = re.search(
                r'NEXT_PUBLIC_PUSHER_KEY[^}]*?default\("([a-f0-9]+)"\)',
                js_resp.text,
            )
        except re.error:  # pragma: no cover — fixed regex cannot raise; defensive only
            continue
        if match:
            return match.group(1)

    return None


def resolve_pusher_key(
    *,
    force_discover: bool = False,
    http_client: _HttpClient | None = None,
    cache: PusherKeyCache | None = None,
) -> str:
    """Return the cached/compiled-in key without I/O, or discover on forced calls.

    Discovery scans at most 15 JS bundles in ten seconds, with three seconds per
    request; failure uses the default. Resolved keys are cached for the process.

    Args:
        force_discover: Bypass cache and scan the live page, e.g. after a
            ``pusher:error`` indicating key rotation.
        http_client: Injectable client; defaults to a browser-like requests session.
        cache: Cache to read/populate; defaults to the shared process-wide cache.
    """
    key_cache = cache if cache is not None else _pusher_key_cache

    if not force_discover:
        if key_cache.key is None:
            key_cache.key = _PUSHER_DEFAULT_KEY
        return key_cache.key

    client = http_client or _RequestsHttpClient()
    try:
        key = _discover_pusher_key(client)
    finally:
        client.close()

    if key is None:
        key = _PUSHER_DEFAULT_KEY

    key_cache.key = key
    return key


def get_pusher_ws_url(
    *,
    force_discover: bool = False,
    http_client: _HttpClient | None = None,
) -> str:
    """Build the Pusher WebSocket URL from the current app key.

    Args:
        force_discover: Refresh from Kick's live JS; otherwise use cached/default
            key without I/O.
        http_client: HTTP client carrying downloader session settings.
    """
    key = resolve_pusher_key(
        force_discover=force_discover,
        http_client=http_client,
    )
    return _PUSHER_WS_TEMPLATE.format(key=key)

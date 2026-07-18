# SPDX-License-Identifier: MIT

"""Kick Pusher application-key discovery.

The Pusher app key is shipped in Kick's public Next.js JavaScript bundle
(``NEXT_PUBLIC_PUSHER_KEY``). It is not a secret — it grants only anonymous,
read-only subscription to public chatroom channels.

This module fetches Kick's homepage, scans linked JS chunks for the current key,
caches the result for the process lifetime, and falls back to a compiled-in
default when discovery fails or is unavailable. It is separate from
:mod:`chat_downloader.sites.kick.constants` so that the constant module stays
free of network I/O and so the discovery logic can be unit-tested with a fake
HTTP client.
"""

from __future__ import annotations

import contextlib
import re
from typing import Protocol, cast

import requests

#: Default Pusher application key compiled into Kick's JS bundle.
#: This is not a secret; it is shipped in Kick's public JavaScript bundle and
#: grants only anonymous, read-only subscription to public chatroom channels.
_PUSHER_DEFAULT_KEY = "32cbd69e4b950bf97679"

#: Pusher websocket URL template, formatted with the resolved app key.
_PUSHER_WS_TEMPLATE = (
    "wss://ws-us2.pusher.com/app/{key}?protocol=7&client=js&version=7.6.0&flash=false"
)


class PusherKeyCache:
    """Holder for a discovered Pusher app key.

    Encapsulates the discovered-key cache so callers can inject an isolated
    instance (tests) instead of mutating shared module state. ``key`` is
    ``None`` until a key has been resolved.
    """

    def __init__(self) -> None:
        """Initialise an empty cache with no resolved key."""
        self.key: str | None = None


#: Process-wide cache used by :func:`resolve_pusher_key` when no cache is passed.
_pusher_key_cache = PusherKeyCache()


class _HttpResponse(Protocol):
    """Minimal response shape required by Pusher-key discovery."""

    ok: bool
    text: str


class _HttpClient(Protocol):
    """Minimal HTTP client shape required by Pusher-key discovery."""

    def get(self, url: str, *, timeout: float) -> _HttpResponse: ...
    def close(self) -> None: ...


class _RequestsHttpClient:
    """Thin ``requests`` adapter used by default discovery."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._owns_session = session is None
        self._session = session or requests.Session()
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

    def get(self, url: str, *, timeout: float) -> _HttpResponse:
        return cast("_HttpResponse", self._session.get(url, timeout=timeout))

    def close(self) -> None:
        if self._owns_session:
            with contextlib.suppress(OSError, RuntimeError):
                self._session.close()


def _is_kick_origin(url: str) -> bool:
    """Return True if *url* is an HTTPS URL on the ``kick.com`` domain.

    Used to constrain which script URLs the Pusher-key discovery loop will
    fetch, so a tampered homepage cannot redirect it to an arbitrary host.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "kick.com" or host.endswith(".kick.com")


def _discover_pusher_key(http_client: _HttpClient) -> str | None:
    """Scan Kick's homepage and JS bundles for the current Pusher key.

    Args:
        http_client: Client used to fetch the homepage and candidate bundles.

    Returns:
        The discovered key, or ``None`` if no bundle contained it.
    """
    homepage = http_client.get("https://kick.com/", timeout=10)
    if not homepage.ok:
        return None

    # Find all JS chunk URLs in the page (scan at most 15 chunks)
    script_urls = re.findall(r'<script[^>]*src="([^"]+\.js)"[^>]*>', homepage.text)
    for url in script_urls[:15]:
        abs_url = url if url.startswith("http") else "https://kick.com" + url
        # Only fetch scripts served over HTTPS from Kick's own domain.
        # Without this guard a tampered/MITM'd homepage could point the
        # loader at an arbitrary host (SSRF, e.g. cloud metadata endpoints).
        # ``*.kick.com`` is allowed so CDN-hosted bundles still resolve.
        if not _is_kick_origin(abs_url):
            continue
        try:
            js_resp = http_client.get(abs_url, timeout=10)
        except OSError:
            continue
        if not js_resp.ok:
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
    """Return the current Pusher application key, discovering it if needed.

    The key lives in Kick's Next.js JS bundle as ``NEXT_PUBLIC_PUSHER_KEY``.
    It is stable across page loads but can change when Kick rebuilds their
    frontend. This function fetches the homepage on first call to extract the
    current value, falling back to the compiled-in default if discovery fails.

    Discovery scans at most 15 JS bundles with a per-bundle 10s timeout.
    Once resolved the key is cached for the process lifetime.

    Args:
        force_discover: If True, skip the cache and re-discover from the live
            page. Useful when a ``pusher:error`` suggests the key has rotated.
        http_client: Optional HTTP client for dependency injection (tests
            supply a fake). Defaults to a browser-like ``requests`` session.
        cache: Optional key cache to read/populate. Defaults to the shared
            process-wide cache.

    Returns:
        The Pusher app key string.
    """
    key_cache = cache if cache is not None else _pusher_key_cache

    if key_cache.key is not None and not force_discover:
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
    """Return the Pusher websocket URL with the current app key.

    Args:
        force_discover: If True, force re-discovery of the app key from
            Kick's live JS bundle before building the URL.
        http_client: Optional HTTP client carrying downloader session settings.

    Returns:
        The full Pusher WebSocket URL.
    """
    key = resolve_pusher_key(
        force_discover=force_discover,
        http_client=http_client,
    )
    return _PUSHER_WS_TEMPLATE.format(key=key)

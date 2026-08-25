# SPDX-License-Identifier: MIT

"""Init-time configuration safety checks for ChatDownloader."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import requests

from chat_downloader.debugging import log
from chat_downloader.errors import InvalidParameter

_COOKIE_AUTH_TARGETS = (
    "https://www.youtube.com/",
    "https://www.twitch.tv/",
)


def _is_loopback_host(host: str) -> bool:
    """Return True only for genuine loopback hosts.

    Uses :mod:`ipaddress` so the whole ``127.0.0.0/8`` range and ``::1`` are
    recognized, while spoofed names like ``127.0.0.1.attacker.com`` (which a
    naive ``startswith("127.")`` check would wrongly accept) are rejected.
    ``urlparse`` already strips IPv6 brackets, so ``::1`` arrives bare here.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_proxy_cookie_safety(proxy: str | None, cookies: str | None) -> None:
    """Guard against leaking cookie credentials to a non-local proxy.

    Warns when a loopback proxy is combined with cookie authentication (the
    credentials stay on the local machine) and raises for any other proxy,
    where the credentials would be exposed to a remote process.

    Args:
        proxy: The configured proxy URL, if any.
        cookies: Path to a cookies file, if cookie auth is in use.

    Raises:
        InvalidParameter: If a non-loopback proxy is used with cookies.
    """
    if cookies is None or proxy == "":
        return

    effective_proxies = {proxy} if proxy is not None else _environment_proxies()
    if not effective_proxies:
        return

    remote_proxies = [
        proxy_url
        for proxy_url in effective_proxies
        if not _is_loopback_host(urlparse(proxy_url).hostname or "")
    ]
    if not remote_proxies:
        log(
            "warning",
            "Using a local proxy with cookie authentication; "
            "credentials will be visible to the local proxy process.",
        )
        return

    msg = (
        "A proxy must not be used with cookie authentication: "
        "credentials would be exposed to the proxy."
    )
    raise InvalidParameter(msg)


def _environment_proxies() -> set[str]:
    """Return environment proxies effective for cookie-authenticated sites."""
    proxies: set[str] = set()
    for target_url in _COOKIE_AUTH_TARGETS:
        environment = requests.utils.get_environ_proxies(target_url)
        selected = requests.utils.select_proxy(target_url, environment)
        if selected:
            proxies.add(selected)
    return proxies

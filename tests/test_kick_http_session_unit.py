# SPDX-License-Identifier: MIT
from __future__ import annotations

from chat_downloader.sites.kick.http_session import (
    _plain_browser_headers,
    _try_curl_cffi,
)


def test_curl_cffi_uses_moving_chrome_alias_without_overriding_identity() -> None:
    session = _try_curl_cffi()
    assert session is not None
    try:
        assert session.impersonate == "chrome"
        assert "User-Agent" not in session.headers
    finally:
        session.close()


def test_plain_fallback_supplies_browser_identity() -> None:
    headers = _plain_browser_headers()

    assert "Chrome/" in headers["User-Agent"]
    assert headers["Accept"] == "application/json, text/plain, */*"

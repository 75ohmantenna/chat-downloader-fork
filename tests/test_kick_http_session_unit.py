# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from chat_downloader.sites.kick import http_session
from chat_downloader.sites.kick.http_session import (
    _plain_browser_headers,
    _try_curl_cffi,
)


@pytest.mark.parametrize("backend", ["curl", "cloudscraper", "plain"])
@pytest.mark.parametrize("configured", [False, True], ids=["defaults", "explicit"])
def test_factory_selects_backend_and_applies_session_configuration(
    monkeypatch, backend, configured
):
    with requests.Session() as session:
        session.headers.update({"X-Existing": "retained", "Accept": "original"})
        session.proxies["http"] = "http://resident.test:8080"
        curl = Mock(return_value=session if backend == "curl" else None)
        scraper = Mock(return_value=session if backend == "cloudscraper" else None)
        plain = Mock(return_value=session)
        monkeypatch.setattr(http_session, "_try_curl_cffi", curl)
        monkeypatch.setattr(http_session, "_try_cloudscraper", scraper)
        monkeypatch.setattr(http_session, "_make_plain_session", plain)
        options = (
            {
                "trust_env": False,
                "proxy": {"https": "http://explicit.test:8080"},
                "extra_headers": {"Accept": "application/json", "X-Trace": "test"},
            }
            if configured
            else {}
        )

        result = http_session.create_kick_session(**options)

        assert result is session
        assert session.trust_env is (not configured)
        assert session.proxies == {
            "http": "http://resident.test:8080",
            **({"https": "http://explicit.test:8080"} if configured else {}),
        }
        assert session.headers["X-Existing"] == "retained"
        assert session.headers["Accept"] == (
            "application/json" if configured else "original"
        )
        assert session.headers.get("X-Trace") == ("test" if configured else None)
        curl.assert_called_once_with()
        assert scraper.call_count == int(backend != "curl")
        assert plain.call_count == int(backend == "plain")


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

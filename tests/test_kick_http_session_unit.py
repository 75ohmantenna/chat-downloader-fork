# SPDX-License-Identifier: MIT
from __future__ import annotations

import gzip
import re
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest
import requests
from curl_cffi import Curl

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


def test_loaded_curl_backend_uses_patched_native_libraries() -> None:
    with closing(Curl()) as curl:
        native_version = curl.version().decode("ascii")

    libcurl = re.search(r"\blibcurl/(\d+)\.(\d+)\.(\d+)", native_version)
    assert libcurl is not None, native_version
    assert tuple(map(int, libcurl.groups())) >= (8, 23, 0), native_version
    if "brotli/" in native_version:
        brotli = re.search(r"\bbrotli/(\d+)\.(\d+)\.(\d+)", native_version)
        assert brotli is not None, native_version
        assert tuple(map(int, brotli.groups())) >= (1, 2, 1), native_version


@pytest.mark.parametrize("encoding", ["identity", "gzip", "br"])
def test_real_kick_session_decodes_local_json_responses(encoding) -> None:
    plain = b'{"message":"decoded safely"}'
    # A valid uncompressed Brotli block keeps the test independent of an encoder.
    bodies = {
        "identity": plain,
        "gzip": gzip.compress(plain),
        "br": b'\x8b\r\x80{"message":"decoded safely"}\x03',
    }
    observed_headers = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed_headers.append(self.headers.get("X-Transport-Probe"))
            body = bodies[encoding]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        try:
            with closing(
                http_session.create_kick_session(
                    trust_env=False,
                    extra_headers={"X-Transport-Probe": "preserved"},
                )
            ) as session:
                response = session.get(
                    f"http://127.0.0.1:{server.server_port}/", timeout=5
                )
                assert response.status_code == 200
                assert response.content == plain
                assert response.json() == {"message": "decoded safely"}
                assert observed_headers == ["preserved"]
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_plain_fallback_supplies_browser_identity() -> None:
    headers = _plain_browser_headers()

    assert "Chrome/" in headers["User-Agent"]
    assert headers["Accept"] == "application/json, text/plain, */*"

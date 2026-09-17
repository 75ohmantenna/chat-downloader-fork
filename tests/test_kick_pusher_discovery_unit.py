# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chat_downloader.sites.kick import pusher_discovery
from chat_downloader.sites.kick.pusher_discovery import (
    _PUSHER_DEFAULT_KEY,
    PusherKeyCache,
    resolve_pusher_key,
)
from tests.kick_helpers import FakeKickSession, FakeResponse

HOME = "https://kick.com/"
KEY = "a1b2c3d4e5f6"
BUNDLE = f'NEXT_PUBLIC_PUSHER_KEY={{default("{KEY}")}}'


def _client(*scripts, homepage_status=200, bundles=None):
    homepage = "".join(f'<script src="{script}"></script>' for script in scripts)
    return FakeKickSession(
        [FakeResponse(homepage_status, text=homepage)]
        + (bundles if bundles is not None else [FakeResponse(200, text=BUNDLE)])
    )


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pusher_discovery._pusher_key_cache, "key", None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://kick.com/app.js", False),
        ("https://kick.com/app.js", True),
        ("https://static.kick.com/app.js", True),
        ("https://evil.com/app.js", False),
    ],
)
def test_is_kick_origin(url, expected) -> None:
    assert pusher_discovery._is_kick_origin(url) is expected


def test_resolve_pusher_key_uses_default_without_network() -> None:
    client = FakeKickSession([])
    assert resolve_pusher_key(http_client=client) == _PUSHER_DEFAULT_KEY
    assert client.requested_urls == []
    assert client.close_calls == 0


def test_forced_discovery_uses_requests_adapter_by_default(monkeypatch) -> None:
    session = _client("/adapter.js")
    session.headers = {}
    monkeypatch.setattr(pusher_discovery.requests, "Session", lambda: session)
    assert resolve_pusher_key(force_discover=True) == KEY
    assert HOME in session.requested_urls


def test_requests_adapter_reuses_downloader_session_without_closing() -> None:
    session = pusher_discovery.requests.Session()
    session.close = MagicMock()
    pusher_discovery._RequestsHttpClient(session).close()
    session.close.assert_not_called()


@pytest.mark.parametrize("configured_timeout", [None, (1.0, 2.0)])
def test_requests_adapter_timeout_and_redirect_policy(configured_timeout) -> None:
    session = MagicMock()
    session.get.return_value = FakeResponse(200)
    adapter = pusher_discovery._RequestsHttpClient(
        session, configured_timeout=configured_timeout
    )
    adapter.get(HOME, timeout=3.0)
    session.get.assert_called_once_with(
        HOME, timeout=configured_timeout or 3.0, allow_redirects=False
    )


@pytest.mark.parametrize("injected", [False, True])
def test_discovery_caches_result_and_closes_client(injected) -> None:
    cache = PusherKeyCache() if injected else pusher_discovery._pusher_key_cache
    client = _client("/_next/static/chunk.js")
    options = {"cache": cache} if injected else {}
    key1 = resolve_pusher_key(force_discover=True, http_client=client, **options)
    key2 = resolve_pusher_key(http_client=FakeKickSession([]), **options)
    assert key1 == key2 == cache.key == KEY
    assert client.close_calls == 1
    assert HOME in client.requested_urls


def test_resolve_pusher_key_force_discover_bypasses_cache(monkeypatch) -> None:
    monkeypatch.setattr(pusher_discovery._pusher_key_cache, "key", "oldkey")
    assert (
        resolve_pusher_key(force_discover=True, http_client=_client("/app.js")) == KEY
    )


@pytest.mark.parametrize("status", [500, 302])
def test_discovery_rejects_failed_or_redirected_homepage(status) -> None:
    client = _client("/app.js", homepage_status=status)
    assert (
        resolve_pusher_key(force_discover=True, http_client=client)
        == _PUSHER_DEFAULT_KEY
    )
    assert client.requested_urls == [HOME]


def test_discovery_homepage_request_failure_closes_client() -> None:
    client = FakeKickSession([ConnectionError("unreachable")])
    assert (
        resolve_pusher_key(force_discover=True, http_client=client)
        == _PUSHER_DEFAULT_KEY
    )
    assert client.close_calls == 1


def test_discovery_stops_scanning_when_budget_expires(monkeypatch) -> None:
    client = _client("/app.js")
    monkeypatch.setattr(
        pusher_discovery.time, "monotonic", MagicMock(side_effect=[0.0, 11.0])
    )
    assert (
        resolve_pusher_key(force_discover=True, http_client=client)
        == _PUSHER_DEFAULT_KEY
    )
    assert client.requested_urls == [HOME]


@pytest.mark.parametrize(
    "bundle", [FakeResponse(200, text="no key here"), FakeResponse(302, text=BUNDLE)]
)
def test_discovery_rejects_missing_key_or_redirected_bundle(bundle) -> None:
    client = _client("/app.js", bundles=[bundle])
    assert (
        resolve_pusher_key(force_discover=True, http_client=client)
        == _PUSHER_DEFAULT_KEY
    )


@pytest.mark.parametrize(
    "script", ["https://evil.com/app.js", "http://kick.com/app.js"]
)
def test_discovery_skips_unsafe_script_origins(script) -> None:
    client = _client(script)
    assert (
        resolve_pusher_key(force_discover=True, http_client=client)
        == _PUSHER_DEFAULT_KEY
    )
    assert client.requested_urls == [HOME]


@pytest.mark.parametrize("bad", [ConnectionError("unreachable"), FakeResponse(500)])
def test_discovery_skips_failed_bundle_and_uses_next(bad) -> None:
    client = _client(
        "/_next/static/bad.js",
        "/_next/static/good.js",
        bundles=[bad, FakeResponse(200, text=BUNDLE)],
    )
    assert resolve_pusher_key(force_discover=True, http_client=client) == KEY
    assert client.requested_urls == [
        HOME,
        f"{HOME}_next/static/bad.js",
        f"{HOME}_next/static/good.js",
    ]

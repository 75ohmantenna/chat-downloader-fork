# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chat_downloader.sites.kick import pusher_discovery as pd
from tests.kick_helpers import FakeKickSession, FakeResponse

HOME, KEY = "https://kick.com/", "a1b2c3d4e5f6"
BUNDLE = f'NEXT_PUBLIC_PUSHER_KEY={{default("{KEY}")}}'


def _client(*scripts, status=200, bundles=None):
    homepage = "".join(f'<script src="{script}"></script>' for script in scripts)
    return FakeKickSession(
        [FakeResponse(status, text=homepage)]
        + (bundles if bundles is not None else [FakeResponse(200, text=BUNDLE)])
    )


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(pd._pusher_key_cache, "key", None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://kick.com/app.js", False),
        ("https://kick.com/app.js", True),
        ("https://static.kick.com/app.js", True),
        ("https://evil.com/app.js", False),
    ],
)
def test_kick_origin(url, expected):
    assert pd._is_kick_origin(url) is expected


def test_default_discovery_network_and_ownership_policy(monkeypatch):
    client = FakeKickSession([])
    assert pd.resolve_pusher_key(http_client=client) == pd._PUSHER_DEFAULT_KEY
    assert client.requested_urls == []
    assert client.close_calls == 0
    session = _client("/adapter.js")
    session.headers = {}
    monkeypatch.setattr(pd.requests, "Session", lambda: session)
    assert pd.resolve_pusher_key(force_discover=True) == KEY
    assert HOME in session.requested_urls


@pytest.mark.parametrize("timeout", [None, (1.0, 2.0)])
def test_borrowed_requests_adapter_timeout_redirect_and_close_policy(timeout):
    session = MagicMock()
    session.get.return_value = FakeResponse(200)
    adapter = pd._RequestsHttpClient(session, configured_timeout=timeout)
    adapter.get(HOME, timeout=3.0)
    session.get.assert_called_once_with(
        HOME, timeout=timeout or 3.0, allow_redirects=False
    )
    adapter.close()
    session.close.assert_not_called()


@pytest.mark.parametrize("injected", [False, True])
def test_discovery_bypasses_old_cache_then_caches_and_closes(injected):
    cache = pd.PusherKeyCache() if injected else pd._pusher_key_cache
    cache.key = "oldkey"
    client = _client("/_next/static/chunk.js")
    options = {"cache": cache} if injected else {}
    assert (
        pd.resolve_pusher_key(force_discover=True, http_client=client, **options) == KEY
    )
    assert pd.resolve_pusher_key(http_client=FakeKickSession([]), **options) == KEY
    assert cache.key == KEY
    assert client.close_calls == 1
    assert HOME in client.requested_urls


@pytest.mark.parametrize(
    "scenario",
    [
        "homepage-500",
        "homepage-302",
        "unreachable",
        "budget",
        "missing-key",
        "redirected-bundle",
        "foreign-origin",
        "insecure-origin",
    ],
)
def test_discovery_fallback_preserves_request_boundaries(monkeypatch, scenario):
    scripts = {
        "foreign-origin": "https://evil.com/app.js",
        "insecure-origin": "http://kick.com/app.js",
    }
    client = _client(
        scripts.get(scenario, "/app.js"),
        status=int(scenario[-3:]) if scenario.startswith("homepage-") else 200,
        bundles=[FakeResponse(302, text=BUNDLE)]
        if scenario == "redirected-bundle"
        else [FakeResponse(200, text="no key here")],
    )
    if scenario == "unreachable":
        client = FakeKickSession([ConnectionError("unreachable")])
    if scenario == "budget":
        monkeypatch.setattr(pd.time, "monotonic", MagicMock(side_effect=[0.0, 11.0]))
    assert (
        pd.resolve_pusher_key(force_discover=True, http_client=client)
        == pd._PUSHER_DEFAULT_KEY
    )
    assert client.close_calls == 1
    if scenario not in {"missing-key", "redirected-bundle"}:
        assert client.requested_urls == [HOME]


@pytest.mark.parametrize("bad", [ConnectionError("unreachable"), FakeResponse(500)])
def test_failed_bundle_does_not_prevent_next_candidate(bad):
    client = _client(
        "/_next/static/bad.js",
        "/_next/static/good.js",
        bundles=[bad, FakeResponse(200, text=BUNDLE)],
    )
    assert pd.resolve_pusher_key(force_discover=True, http_client=client) == KEY
    assert client.requested_urls == [
        HOME,
        f"{HOME}_next/static/bad.js",
        f"{HOME}_next/static/good.js",
    ]

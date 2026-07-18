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


class _FakeResponse:
    """Stub response satisfying the discovery HTTP-response protocol."""

    __slots__ = ("ok", "text")

    def __init__(self, ok: bool, text: str) -> None:
        self.ok = ok
        self.text = text


class _FakeClient:
    """In-memory HTTP client for testing Pusher-key discovery."""

    def __init__(
        self,
        responses: dict[str, _FakeResponse],
        *,
        errors: set[str] | None = None,
    ) -> None:
        self.responses = responses
        self.errors = errors or set()
        self.closed = False
        self.requested_urls: list[str] = []

    def get(self, url: str, *, timeout: float) -> _FakeResponse:
        del timeout
        self.requested_urls.append(url)
        if url in self.errors:
            msg = f"Unreachable URL: {url}"
            raise ConnectionError(msg)
        response = self.responses.get(url)
        if response is None:
            msg = f"Unexpected URL: {url}"
            raise ConnectionError(msg)
        return response

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the process-wide Pusher-key cache around every test."""
    monkeypatch.setattr(pusher_discovery._pusher_key_cache, "key", None)


def test_is_kick_origin_rejects_non_https_url() -> None:
    assert pusher_discovery._is_kick_origin("http://kick.com/app.js") is False


def test_is_kick_origin_accepts_kick_domain() -> None:
    assert pusher_discovery._is_kick_origin("https://kick.com/app.js") is True


def test_is_kick_origin_accepts_kick_subdomain() -> None:
    assert pusher_discovery._is_kick_origin("https://static.kick.com/app.js") is True


def test_is_kick_origin_rejects_other_domain() -> None:
    assert pusher_discovery._is_kick_origin("https://evil.com/app.js") is False


def test_resolve_pusher_key_uses_requests_adapter_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the production ``requests`` adapter path with a fake session."""
    calls: list[str] = []

    class _FakeRequestsSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, url: str, *, timeout: float) -> _FakeResponse:
            del timeout
            calls.append(url)
            if url == "https://kick.com/":
                return _FakeResponse(
                    True,
                    '<script src="/adapter.js"></script>',
                )
            return _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("feedfacedead")}',
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(pusher_discovery.requests, "Session", _FakeRequestsSession)

    key = resolve_pusher_key()

    assert key == "feedfacedead"
    assert "https://kick.com/" in calls


def test_requests_adapter_reuses_downloader_session_without_closing() -> None:
    session = pusher_discovery.requests.Session()
    session.close = MagicMock()
    adapter = pusher_discovery._RequestsHttpClient(session)

    adapter.close()

    session.close.assert_not_called()


def test_resolve_pusher_key_discovers_key_from_bundle() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="/_next/static/chunk.js"></script>',
            ),
            "https://kick.com/_next/static/chunk.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("a1b2c3d4e5f6")}',
            ),
        }
    )

    key = resolve_pusher_key(http_client=client)

    assert key == "a1b2c3d4e5f6"
    assert client.closed
    assert "https://kick.com/" in client.requested_urls


def test_resolve_pusher_key_caches_result() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="/app.js"></script>',
            ),
            "https://kick.com/app.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("abcdef123456")}',
            ),
        }
    )

    key1 = resolve_pusher_key(http_client=client)
    key2 = resolve_pusher_key(http_client=_FakeClient({}))

    assert key1 == key2 == "abcdef123456"


def test_resolve_pusher_key_uses_injected_cache() -> None:
    cache = PusherKeyCache()
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="/app.js"></script>',
            ),
            "https://kick.com/app.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("cafebabe0001")}',
            ),
        }
    )

    key1 = resolve_pusher_key(http_client=client, cache=cache)
    # A pre-seeded injected cache is returned without re-discovering.
    key2 = resolve_pusher_key(http_client=_FakeClient({}), cache=cache)

    assert key1 == key2 == "cafebabe0001"
    assert cache.key == "cafebabe0001"


def test_resolve_pusher_key_force_discover_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pusher_discovery._pusher_key_cache, "key", "oldkey")

    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="/app.js"></script>',
            ),
            "https://kick.com/app.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("0123456789ab")}',
            ),
        }
    )

    key = resolve_pusher_key(force_discover=True, http_client=client)

    assert key == "0123456789ab"


def test_resolve_pusher_key_falls_back_when_homepage_fails() -> None:
    client = _FakeClient({"https://kick.com/": _FakeResponse(False, "")})

    key = resolve_pusher_key(http_client=client)

    assert key == _PUSHER_DEFAULT_KEY


def test_resolve_pusher_key_falls_back_when_no_bundle_matches() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="/app.js"></script>',
            ),
            "https://kick.com/app.js": _FakeResponse(True, "no key here"),
        }
    )

    key = resolve_pusher_key(http_client=client)

    assert key == _PUSHER_DEFAULT_KEY


def test_resolve_pusher_key_skips_foreign_script_urls() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="https://evil.com/app.js"></script>',
            ),
        }
    )

    key = resolve_pusher_key(http_client=client)

    assert key == _PUSHER_DEFAULT_KEY


def test_resolve_pusher_key_skips_http_script_urls() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                '<script src="http://kick.com/app.js"></script>',
            ),
        }
    )

    key = resolve_pusher_key(http_client=client)

    assert key == _PUSHER_DEFAULT_KEY


def test_resolve_pusher_key_skips_unreachable_bundle_and_uses_next() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                (
                    '<script src="/_next/static/bad.js"></script>'
                    '<script src="/_next/static/good.js"></script>'
                ),
            ),
            "https://kick.com/_next/static/good.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("123abcdef012")}',
            ),
        },
        errors={"https://kick.com/_next/static/bad.js"},
    )

    key = resolve_pusher_key(http_client=client)

    assert key == "123abcdef012"


def test_resolve_pusher_key_skips_non_ok_bundle() -> None:
    client = _FakeClient(
        {
            "https://kick.com/": _FakeResponse(
                True,
                (
                    '<script src="/_next/static/missing.js"></script>'
                    '<script src="/_next/static/good.js"></script>'
                ),
            ),
            "https://kick.com/_next/static/missing.js": _FakeResponse(False, ""),
            "https://kick.com/_next/static/good.js": _FakeResponse(
                True,
                'NEXT_PUBLIC_PUSHER_KEY={default("0a1b2c3d4e5f")}',
            ),
        }
    )

    key = resolve_pusher_key(http_client=client)

    assert key == "0a1b2c3d4e5f"

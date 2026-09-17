# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.sites.kick import api_client
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import (
    KickCountryBlocked,
    KickError,
    KickServerError,
)
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    load_text_fixture,
)

CLIP_ID = "clip_01M0BHEHDAX2NEAGXG0DA8V9S5"


def _client(
    responses: list[Any],
    *,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> tuple[KickApiClient, FakeKickSession]:
    session = FakeKickSession(responses)
    return (
        KickApiClient(session=session, mobile_session=session, timeout=timeout),
        session,
    )


def test_fetch_channel_success_uses_owned_session_and_timeout() -> None:
    payload = load_fixture("channel_live.json")
    client, session = _client([FakeResponse(200, payload)], timeout=(3.0, 7.0))

    data = client.fetch_channel("examplechannel")

    assert data["id"] == 12345
    assert session.calls == [
        (
            "https://kick.com/api/v2/channels/examplechannel",
            {"params": None, "timeout": (3.0, 7.0)},
        )
    ]


def test_primary_requests_resolve_bearer_token_lazily() -> None:
    payload = load_fixture("channel_live.json")
    tokens = iter(["first-token", "rotated-token"])
    session = FakeKickSession([FakeResponse(200, payload), FakeResponse(200, payload)])
    client = KickApiClient(
        session=session,
        bearer_token_provider=lambda: next(tokens),
    )

    client.fetch_channel("examplechannel")
    client.fetch_channel("examplechannel")

    assert [call[1]["headers"] for call in session.calls] == [
        {"Authorization": "Bearer first-token"},
        {"Authorization": "Bearer rotated-token"},
    ]


def test_explicit_authorization_takes_precedence_over_cookie_token() -> None:
    payload = load_fixture("channel_live.json")
    session = FakeKickSession([FakeResponse(200, payload)])

    def unexpected_provider() -> str:
        raise AssertionError("cookie token provider should not be called")

    client = KickApiClient(
        extra_headers={"authorization": "Bearer explicit"},
        bearer_token_provider=unexpected_provider,
        session=session,
    )

    client.fetch_channel("examplechannel")

    assert "headers" not in session.calls[0][1]


@pytest.mark.parametrize("token", ["", "has spaces", "has\ttab", "has\nnewline", "é"])
def test_primary_requests_reject_unsafe_bearer_tokens(token: str) -> None:
    payload = load_fixture("channel_live.json")
    session = FakeKickSession([FakeResponse(200, payload)])
    client = KickApiClient(
        session=session,
        bearer_token_provider=lambda: token,
    )

    client.fetch_channel("examplechannel")

    assert "headers" not in session.calls[0][1]


def test_client_copies_proxy_and_header_configuration(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    session = FakeKickSession([])

    def fake_create(**kwargs: Any) -> FakeKickSession:
        captured.update(kwargs)
        return session

    monkeypatch.setattr(api_client, "create_kick_session", fake_create)
    proxy = {"https": "http://proxy.example:8080"}
    headers = {"Authorization": "secret"}
    KickApiClient(proxy=proxy, extra_headers=headers)
    proxy["https"] = "changed"
    headers["Authorization"] = "changed"

    assert captured == {
        "proxy": {"https": "http://proxy.example:8080"},
        "extra_headers": {"Authorization": "secret"},
        "trust_env": True,
    }


@pytest.mark.parametrize("injected", [False, True])
def test_mobile_session_is_origin_isolated(monkeypatch: Any, injected: bool) -> None:
    payload = load_fixture("clip_metadata_mobile.json")
    primary = FakeKickSession([])
    primary.headers = {"Authorization": "Bearer resident-secret"}
    mobile = FakeKickSession([FakeResponse(200, payload)])
    sessions = iter([mobile] if injected else [primary, mobile])
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return next(sessions)

    monkeypatch.setattr(api_client, "create_kick_session", create)
    proxy = {"https": "http://proxy.example:8080"}
    sensitive = {
        "Authorization": "Bearer secret",
        "Cookie": "session=secret",
        "X-Api-Key": "secret",
        "X-Custom": "Bearer secret",
    }
    safe = {"User-Agent": "safe-agent", "X-Trace": "safe-trace"}
    headers = {**sensitive, **safe}
    options = (
        {"session": primary}
        if injected
        else {
            "proxy": proxy,
            "extra_headers": headers,
            "timeout": (3.0, 7.0),
            "trust_env": False,
        }
    )
    client = KickApiClient(**options)
    assert client.fetch_mobile_clip_metadata(CLIP_ID) == payload
    assert primary.calls == []
    if injected:
        assert captured == [{"proxy": None, "extra_headers": None, "trust_env": True}]
    else:
        assert captured == [
            {"proxy": proxy, "extra_headers": headers, "trust_env": False},
            {"proxy": proxy, "extra_headers": safe, "trust_env": False},
        ]
        assert mobile.calls == [
            (
                f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
                {
                    "params": None,
                    "timeout": (3.0, 7.0),
                    "headers": dict.fromkeys(sensitive),
                },
            )
        ]
    client.close()
    assert primary.close_calls == mobile.close_calls == 1


def test_client_close_is_idempotent_and_use_after_close_fails() -> None:
    client, session = _client([])

    client.close()
    client.close()

    assert session.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        client.fetch_channel("examplechannel")


def test_client_close_logs_known_close_error(caplog: Any) -> None:
    class BrokenSession:
        @staticmethod
        def get(_url: str, **_kwargs: object) -> Any:
            raise AssertionError("not called")

        @staticmethod
        def close() -> None:
            raise OSError("close failed")

    caplog.set_level("DEBUG", logger=api_client.logger.name)
    KickApiClient(session=BrokenSession()).close()  # type: ignore[arg-type]

    assert "close failed" in caplog.text


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (FakeResponse(404, {"message": "not found"}), UserNotFound, None),
        *[
            (
                FakeResponse(
                    status,
                    text=load_text_fixture("cloudflare_challenge.html"),
                    content_type="text/html",
                    malformed=True,
                ),
                CaptchaChallengeRequired,
                None,
            )
            for status in (403, 503)
        ],
        (FakeResponse(403, text="denied"), CaptchaChallengeRequired, None),
        (
            FakeResponse(423, {"message": "denied"}),
            KickCountryBlocked,
            r"country or region \(HTTP 423\)",
        ),
        (
            FakeResponse(
                423,
                text="<html><body>country blocked</body></html>",
                content_type="text/html",
                malformed=True,
            ),
            KickCountryBlocked,
            "HTTP 423",
        ),
        (
            FakeResponse(
                200,
                text="<html><body>nope</body></html>",
                content_type="text/html",
                malformed=True,
            ),
            CaptchaChallengeRequired,
            None,
        ),
        (
            FakeResponse(200, text="garbage", malformed=True),
            KickServerError,
            "malformed JSON",
        ),
        *[
            (FakeResponse(status, {"err": True}), KickServerError, str(status))
            for status in (429, 500, 503)
        ],
        (FakeResponse(418, {"err": True}), KickError, "418"),
        (FakeResponse(200, ["not", "an", "object"]), KickServerError, "JSON object"),
    ],
)
def test_channel_response_errors(response, error, match) -> None:
    client, session = _client([response])
    with pytest.raises(error, match=match):
        client.fetch_channel("blocked")
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("payload", "messages"),
    [
        ({"data": {"messages": [{"id": "a"}, "bad", 7]}}, [{"id": "a"}]),
        ({"data": {}}, []),
    ],
)
def test_preloaded_chat_filters_or_defaults_messages(payload, messages) -> None:
    client, _ = _client([FakeResponse(200, payload)])
    state = client.fetch_preloaded_chat_state("1", "x")
    assert state.messages == messages
    assert state.pinned_message is None


def test_fetch_preloaded_chat_state_preserves_current_pin() -> None:
    payload = load_fixture("preloaded_messages_with_pin.json")
    client, _ = _client([FakeResponse(200, payload)])

    state = client.fetch_preloaded_chat_state("1", "x")

    assert [message["id"] for message in state.messages] == ["preloaded-current"]
    assert state.pinned_message is not None
    assert state.pinned_message["message"]["id"] == "startup-pinned-message"


def test_fetch_preloaded_chat_state_does_not_hide_required_response_errors() -> None:
    client, _ = _client([FakeResponse(500, {"err": True})])

    with pytest.raises(KickServerError):
        client.fetch_preloaded_chat_state("1", "x")


@pytest.mark.parametrize(
    ("method", "identifier", "match"),
    [
        ("fetch_video_metadata", "vod-1", "video not found"),
        ("fetch_clip_metadata", "clip_missing", "clip not found"),
    ],
)
def test_metadata_endpoint_not_found(method, identifier, match) -> None:
    client, _ = _client([FakeResponse(404, {})])
    with pytest.raises(KickError, match=match):
        getattr(client, method)(identifier)


@pytest.mark.parametrize(
    ("method", "fixture", "endpoint"),
    [
        ("fetch_clip_metadata", "clip_metadata.json", "https://kick.com/api/v2"),
        (
            "fetch_mobile_clip_metadata",
            "clip_metadata_mobile.json",
            "https://mobile.kick.com/api/v1",
        ),
    ],
)
def test_clip_metadata_endpoints(method, fixture, endpoint) -> None:
    payload = load_fixture(fixture)
    client, session = _client([FakeResponse(200, payload)])
    assert getattr(client, method)(CLIP_ID) == payload
    assert session.calls == [
        (f"{endpoint}/clips/{CLIP_ID}", {"params": None, "timeout": (10.0, 30.0)})
    ]


def test_fetch_message_page_passes_one_pagination_parameter() -> None:
    client, session = _client(
        [
            FakeResponse(200, {"data": {}}),
            FakeResponse(200, {"data": {}}),
            FakeResponse(200, {"data": {}}),
        ]
    )

    client.fetch_message_page("123", cursor="next")
    client.fetch_message_page("123", start_time="2026-01-01T00:00:00.000000Z")
    client.fetch_message_page("123", cursor="")

    assert session.calls[0][1]["params"] == {"cursor": "next"}
    assert session.calls[1][1]["params"] == {
        "start_time": "2026-01-01T00:00:00.000000Z"
    }
    assert session.calls[2][1]["params"] is None


def test_fetch_message_page_rejects_mixed_pagination_parameters() -> None:
    client, session = _client([])

    with pytest.raises(ValueError, match="either cursor or start_time"):
        client.fetch_message_page("123", cursor="older", start_time="newer")

    assert session.calls == []


@pytest.mark.parametrize(
    ("response", "pagination", "error", "match"),
    [
        *[
            (
                FakeResponse(status, {"errors": {"start_time": ["invalid"]}}),
                {"start_time": "2026-01-01T00:00:00Z"},
                api_client.KickForwardHistoryRejected,
                None,
            )
            for status in (400, 422)
        ],
        *[
            (
                FakeResponse(status, {"errors": {"channel": ["bad"]}}),
                {"start_time": "2026-01-01T00:00:00Z"},
                KickError,
                f"unexpected HTTP {status}",
            )
            for status in (400, 422)
        ],
        *[
            (
                response,
                {"start_time": "2026-01-01T00:00:00Z"},
                KickError,
                "unexpected HTTP",
            )
            for response in (
                FakeResponse(400, []),
                FakeResponse(422, {}, malformed=True),
            )
        ],
        (FakeResponse(400, {}), {"cursor": "older"}, KickError, "unexpected HTTP 400"),
        (FakeResponse(403, {}), {}, CaptchaChallengeRequired, None),
    ],
)
def test_message_page_errors(response, pagination, error, match) -> None:
    client, _ = _client([response])
    with pytest.raises(error, match=match):
        client.fetch_message_page("123", **pagination)

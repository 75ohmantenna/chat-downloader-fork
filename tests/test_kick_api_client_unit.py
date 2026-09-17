# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import Mock

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
    message_page,
)

CLIP_ID = "clip_01M0BHEHDAX2NEAGXG0DA8V9S5"
START = "2026-01-01T00:00:00Z"


def _client(responses, **options):
    session = FakeKickSession(responses)
    return KickApiClient(session=session, mobile_session=session, **options), session


@pytest.mark.parametrize(
    ("method", "identifier", "fixture", "endpoint"),
    [
        (
            "fetch_channel",
            "examplechannel",
            "channel_live.json",
            "https://kick.com/api/v2/channels/examplechannel",
        ),
        (
            "fetch_clip_metadata",
            CLIP_ID,
            "clip_metadata.json",
            f"https://kick.com/api/v2/clips/{CLIP_ID}",
        ),
        (
            "fetch_mobile_clip_metadata",
            CLIP_ID,
            "clip_metadata_mobile.json",
            f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
        ),
    ],
)
def test_successful_endpoints_use_owned_session(method, identifier, fixture, endpoint):
    payload = load_fixture(fixture)
    client, session = _client([FakeResponse(200, payload)], timeout=(3.0, 7.0))
    assert getattr(client, method)(identifier) == payload
    assert session.calls == [(endpoint, {"params": None, "timeout": (3.0, 7.0)})]


def test_primary_requests_resolve_bearer_token_lazily():
    tokens = iter(["first-token", "rotated-token"])
    session = FakeKickSession([FakeResponse(200, {}) for _ in range(2)])
    client = KickApiClient(session=session, bearer_token_provider=lambda: next(tokens))
    for _ in range(2):
        client.fetch_channel("examplechannel")
    assert [call[1]["headers"] for call in session.calls] == [
        {"Authorization": "Bearer first-token"},
        {"Authorization": "Bearer rotated-token"},
    ]


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "has spaces",
        "has\ttab",
        "has\nnewline",
        "é",
    ],
)
def test_explicit_authorization_or_unsafe_cookie_omits_request_header(token):
    session = FakeKickSession([FakeResponse(200, {})])
    provider = Mock(return_value=token)
    options = (
        {"extra_headers": {"authorization": "Bearer explicit"}} if token is None else {}
    )
    client = KickApiClient(session=session, bearer_token_provider=provider, **options)
    client.fetch_channel("examplechannel")
    assert "headers" not in session.calls[0][1]
    if token is None:
        provider.assert_not_called()


def test_client_copies_proxy_and_header_configuration(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return FakeKickSession([])

    monkeypatch.setattr(api_client, "create_kick_session", create)
    proxy, headers = {"https": "http://proxy.example:8080"}, {"Authorization": "secret"}
    KickApiClient(proxy=proxy, extra_headers=headers)
    proxy["https"] = headers["Authorization"] = "changed"
    assert captured == {
        "proxy": {"https": "http://proxy.example:8080"},
        "extra_headers": {"Authorization": "secret"},
        "trust_env": True,
    }


@pytest.mark.parametrize("injected", [False, True])
def test_mobile_session_is_origin_isolated(monkeypatch, injected):
    payload = load_fixture("clip_metadata_mobile.json")
    primary, mobile = FakeKickSession([]), FakeKickSession([FakeResponse(200, payload)])
    primary.headers = {"Authorization": "Bearer resident-secret"}
    sessions, captured = iter([mobile] if injected else [primary, mobile]), []

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
    headers = sensitive | safe
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


@pytest.mark.parametrize("broken", [False, True])
def test_close_is_idempotent_and_use_after_close_fails(caplog, broken):
    client, session = _client([])
    if broken:
        session.close = Mock(side_effect=OSError("close failed"))
    caplog.set_level("DEBUG", logger=api_client.logger.name)
    client.close()
    client.close()
    if broken:
        assert "close failed" in caplog.text
    else:
        assert session.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        client.fetch_channel("examplechannel")


@pytest.mark.parametrize(
    ("method", "args", "response", "error", "match"),
    [
        (
            "fetch_preloaded_chat_state",
            ("1", "x"),
            FakeResponse(500, {}),
            KickServerError,
            None,
        ),
        (
            "fetch_video_metadata",
            ("vod-1",),
            FakeResponse(404, {}),
            KickError,
            "video not found",
        ),
        (
            "fetch_clip_metadata",
            ("clip_missing",),
            FakeResponse(404, {}),
            KickError,
            "clip not found",
        ),
        *[
            ("fetch_channel", ("blocked",), response, error, match)
            for response, error, match in [
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
                    "HTTP 423",
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
                (
                    FakeResponse(200, ["not", "an", "object"]),
                    KickServerError,
                    "JSON object",
                ),
            ]
        ],
    ],
)
def test_required_endpoint_errors(method, args, response, error, match):
    client, session = _client([response])
    with pytest.raises(error, match=match):
        getattr(client, method)(*args)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("payload", "ids", "pin"),
    [
        (message_page([{"id": "a"}, "bad", 7]), ["a"], None),
        ({"data": {}}, [], None),
        (
            load_fixture("preloaded_messages_with_pin.json"),
            ["preloaded-current"],
            "startup-pinned-message",
        ),
    ],
)
def test_preloaded_state_filters_messages_and_preserves_pin(payload, ids, pin):
    client, _ = _client([FakeResponse(200, payload)])
    state = client.fetch_preloaded_chat_state("1", "x")
    assert [message["id"] for message in state.messages] == ids
    assert (
        state.pinned_message["message"]["id"] if state.pinned_message else None
    ) == pin


@pytest.mark.parametrize(
    ("pagination", "params"),
    [
        ({"cursor": "next"}, {"cursor": "next"}),
        ({"start_time": START}, {"start_time": START}),
        ({"cursor": ""}, None),
    ],
)
def test_message_page_passes_one_pagination_parameter(pagination, params):
    client, session = _client([FakeResponse(200, {"data": {}})])
    client.fetch_message_page("123", **pagination)
    assert session.calls[0][1]["params"] == params


def test_message_page_rejects_mixed_pagination_parameters():
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
                {"start_time": START},
                api_client.KickForwardHistoryRejected,
                None,
            )
            for status in (400, 422)
        ],
        *[
            (
                FakeResponse(status, {"errors": {"channel": ["bad"]}}),
                {"start_time": START},
                KickError,
                f"unexpected HTTP {status}",
            )
            for status in (400, 422)
        ],
        *[
            (response, {"start_time": START}, KickError, "unexpected HTTP")
            for response in (
                FakeResponse(400, []),
                FakeResponse(422, {}, malformed=True),
            )
        ],
        (FakeResponse(400, {}), {"cursor": "older"}, KickError, "unexpected HTTP 400"),
        (FakeResponse(403, {}), {}, CaptchaChallengeRequired, None),
    ],
)
def test_message_page_errors(response, pagination, error, match):
    client, _ = _client([response])
    with pytest.raises(error, match=match):
        client.fetch_message_page("123", **pagination)

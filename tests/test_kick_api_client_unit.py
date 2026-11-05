# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter

from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.sites.kick import api_client, http_session
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import (
    KickCountryBlocked,
    KickError,
    KickServerError,
    KickVideoRestricted,
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
VIDEO_ID = "b190fd3b-82e6-4b88-b33c-4b6deae0f968"


def _client(responses, **options):
    session = FakeKickSession(responses)
    return KickApiClient(session=session, mobile_session=session, **options), session


@pytest.mark.parametrize("video_id", [VIDEO_ID, VIDEO_ID.upper()])
def test_subscriber_only_video_has_actionable_error(video_id):
    recorded = load_fixture("video_subscriber_only_recorded.json")
    client, _ = _client([FakeResponse(recorded["http_status"], recorded["response"])])
    with pytest.raises(KickVideoRestricted, match="requires a channel subscription"):
        client.fetch_video_metadata(video_id)
    assert client.diagnostics["http_status_counts"] == {"403": 1}


@pytest.mark.parametrize(
    "changes",
    [
        {"uuid": "different"},
        {"uuid": None},
        {"is_private": False},
        {"is_private": 1},
        {"is_private": "true"},
        {"status": "public"},
        {"status": None},
    ],
)
def test_unrecognized_video_denials_remain_terminal_challenges(changes):
    payload = {
        **load_fixture("video_subscriber_only_recorded.json")["response"],
        **changes,
    }
    client, _ = _client([FakeResponse(403, payload)])
    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_video_metadata(VIDEO_ID)


@pytest.mark.parametrize("payload", [None, [], "denied"])
def test_non_object_video_denials_remain_terminal_challenges(payload):
    client, _ = _client([FakeResponse(403, payload)])
    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_video_metadata(VIDEO_ID)


def test_malformed_video_denial_remains_terminal_challenge():
    client, _ = _client([FakeResponse(403, malformed=True)])
    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_video_metadata(VIDEO_ID)


def test_restricted_video_shape_cannot_override_other_resource_denials():
    payload = load_fixture("video_subscriber_only_recorded.json")["response"]
    client, _ = _client([FakeResponse(403, payload)])
    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_channel(VIDEO_ID)


def test_restricted_video_shape_requires_canonical_request_identity():
    payload = load_fixture("video_subscriber_only_recorded.json")["response"]
    payload["uuid"] = "invalid"
    client, _ = _client([FakeResponse(403, payload)])
    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_video_metadata("invalid")


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
    assert session.calls == [
        (
            endpoint,
            {"params": None, "timeout": (3.0, 7.0), "allow_redirects": False},
        )
    ]


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


@pytest.mark.parametrize("injected", [False, True])
def test_mobile_request_does_not_emit_primary_origin_credentials(monkeypatch, injected):
    payload = load_fixture("clip_metadata_mobile.json")
    received = []

    class Adapter(BaseAdapter):
        def send(self, request, **_kwargs):
            received.append(request)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(payload).encode()
            response.request = request
            response.url = request.url
            return response

        def close(self):
            pass

    make_plain = http_session._make_plain_session

    def recorded_plain_session():
        session = make_plain()
        session.mount("https://", Adapter())
        return session

    monkeypatch.setattr(http_session, "_try_curl_cffi", lambda: None)
    monkeypatch.setattr(http_session, "_try_cloudscraper", lambda: None)
    monkeypatch.setattr(http_session, "_make_plain_session", recorded_plain_session)
    sensitive = {
        "Authorization": "Bearer PRIMARY_SENTINEL",
        "Cookie": "session=PRIMARY_SENTINEL",
        "X-Api-Key": "PRIMARY_SENTINEL",
        "X-Custom": "Bearer PRIMARY_SENTINEL",
    }
    safe = {"User-Agent": "safe-agent", "X-Trace": "safe-trace"}
    if injected:
        primary = recorded_plain_session()
        primary.headers.update(sensitive)
        options = {"session": primary, "trust_env": False}
    else:
        options = {"extra_headers": sensitive | safe, "trust_env": False}
    client = KickApiClient(**options)
    try:
        assert client.fetch_mobile_clip_metadata(CLIP_ID) == payload
        assert [request.url for request in received] == [
            f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}"
        ]
        assert "PRIMARY_SENTINEL" not in str(received[0].headers)
        if not injected:
            assert received[0].headers["X-Trace"] == "safe-trace"
    finally:
        client.close()


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
    "phrase",
    ["Just a moment...", "cf-challenge", "Checking your browser before accessing"],
)
def test_json_message_text_does_not_trigger_challenge(phrase: str) -> None:
    payload = {"data": {"messages": [{"id": "1", "content": phrase}]}}
    client, _ = _client([FakeResponse(200, payload)])
    assert client.fetch_message_page("123", cursor=None) == payload


def test_json_channel_title_does_not_trigger_challenge() -> None:
    payload = {"id": 123, "livestream": {"session_title": "Just a moment..."}}
    client, _ = _client([FakeResponse(200, payload)])
    assert client.fetch_channel("examplechannel") == payload


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


@pytest.mark.parametrize("origin", ["primary", "mobile", "web"])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_all_kick_api_origins_reject_redirects_before_decoding(origin, status):
    response = FakeResponse(
        status,
        {"unexpected": "redirect body must not become a successful result"},
    )
    response.headers["Location"] = "https://foreign.invalid/synthetic"
    session = FakeKickSession([response])
    client = KickApiClient(session=session, mobile_session=session, web_session=session)
    with pytest.raises(KickServerError, match="Unexpected HTTP redirect"):
        if origin == "mobile":
            client.fetch_mobile_clip_metadata(CLIP_ID)
        elif origin == "web":
            client.fetch_web_video_metadata("123", VIDEO_ID)
        else:
            client.fetch_channel("example")
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False

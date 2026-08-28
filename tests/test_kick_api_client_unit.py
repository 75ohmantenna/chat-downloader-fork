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


def test_mobile_session_is_origin_isolated_and_omits_sensitive_headers(
    monkeypatch: Any,
) -> None:
    payload = load_fixture("clip_metadata_mobile.json")
    primary_session = FakeKickSession([])
    mobile_session = FakeKickSession([FakeResponse(200, payload)])
    sessions = [primary_session, mobile_session]
    captured: list[dict[str, Any]] = []

    def fake_create(**kwargs: Any) -> FakeKickSession:
        captured.append(kwargs)
        return sessions.pop(0)

    monkeypatch.setattr(api_client, "create_kick_session", fake_create)
    proxy = {"https": "http://proxy.example:8080"}
    headers = {
        "Authorization": "Bearer secret",
        "Cookie": "session=secret",
        "X-Api-Key": "secret",
        "X-Custom": "Bearer secret",
        "User-Agent": "safe-agent",
        "X-Trace": "safe-trace",
    }
    client = KickApiClient(
        proxy=proxy,
        extra_headers=headers,
        timeout=(3.0, 7.0),
        trust_env=False,
    )

    assert client.fetch_mobile_clip_metadata(CLIP_ID) == payload
    assert captured == [
        {
            "proxy": proxy,
            "extra_headers": headers,
            "trust_env": False,
        },
        {
            "proxy": proxy,
            "extra_headers": {
                "User-Agent": "safe-agent",
                "X-Trace": "safe-trace",
            },
            "trust_env": False,
        },
    ]
    assert mobile_session.calls == [
        (
            f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
            {
                "params": None,
                "timeout": (3.0, 7.0),
                "headers": {
                    "Authorization": None,
                    "Cookie": None,
                    "X-Api-Key": None,
                    "X-Custom": None,
                },
            },
        )
    ]

    client.close()

    assert primary_session.close_calls == 1
    assert mobile_session.close_calls == 1


def test_injected_primary_session_is_not_reused_for_mobile_origin(
    monkeypatch: Any,
) -> None:
    payload = load_fixture("clip_metadata_mobile.json")
    primary_session = FakeKickSession([])
    primary_session.headers = {"Authorization": "Bearer resident-secret"}
    mobile_session = FakeKickSession([FakeResponse(200, payload)])
    captured: list[dict[str, Any]] = []

    def fake_create(**kwargs: Any) -> FakeKickSession:
        captured.append(kwargs)
        return mobile_session

    monkeypatch.setattr(api_client, "create_kick_session", fake_create)
    client = KickApiClient(session=primary_session)

    assert client.fetch_mobile_clip_metadata(CLIP_ID) == payload
    assert primary_session.calls == []
    assert captured == [{"proxy": None, "extra_headers": None, "trust_env": True}]

    client.close()

    assert primary_session.close_calls == 1
    assert mobile_session.close_calls == 1


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


def test_fetch_channel_not_found() -> None:
    client, _ = _client([FakeResponse(404, {"message": "not found"})])

    with pytest.raises(UserNotFound):
        client.fetch_channel("ghost")


@pytest.mark.parametrize("status", [403, 503])
def test_challenge_body_is_detected_regardless_of_status(status: int) -> None:
    html = load_text_fixture("cloudflare_challenge.html")
    client, _ = _client(
        [FakeResponse(status, text=html, content_type="text/html", malformed=True)]
    )

    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_channel("blocked")


def test_plain_forbidden_is_challenge() -> None:
    client, _ = _client([FakeResponse(403, None, text="denied")])

    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_channel("blocked")


def test_country_blocked_status_is_actionable_and_terminal() -> None:
    client, session = _client([FakeResponse(423, {"message": "denied"})])

    with pytest.raises(
        KickCountryBlocked,
        match=r"country or region \(HTTP 423\)",
    ):
        client.fetch_channel("blocked")

    assert len(session.calls) == 1


def test_country_blocked_status_outranks_html_challenge_heuristic() -> None:
    client, _ = _client(
        [
            FakeResponse(
                423,
                malformed=True,
                text="<html><body>country blocked</body></html>",
                content_type="text/html",
            )
        ]
    )

    with pytest.raises(KickCountryBlocked, match="HTTP 423"):
        client.fetch_channel("blocked")


def test_html_without_markers_is_challenge() -> None:
    client, _ = _client(
        [
            FakeResponse(
                200,
                malformed=True,
                text="<html><body>nope</body></html>",
                content_type="text/html",
            )
        ]
    )

    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_channel("blocked")


def test_malformed_json_is_transient() -> None:
    client, _ = _client(
        [
            FakeResponse(
                200,
                malformed=True,
                text="garbage",
                content_type="application/json",
            )
        ]
    )

    with pytest.raises(KickServerError, match="malformed JSON"):
        client.fetch_channel("x")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses_are_retryable(status: int) -> None:
    client, _ = _client([FakeResponse(status, {"err": True})])

    with pytest.raises(KickServerError, match=str(status)):
        client.fetch_channel("x")


def test_unexpected_status_is_terminal() -> None:
    client, _ = _client([FakeResponse(418, {"err": True})])

    with pytest.raises(KickError, match="418"):
        client.fetch_channel("x")


def test_non_object_payload_is_transient() -> None:
    client, _ = _client([FakeResponse(200, ["not", "an", "object"])])

    with pytest.raises(KickServerError, match="JSON object"):
        client.fetch_channel("x")


def test_fetch_preloaded_chat_state_filters_non_dict_entries() -> None:
    payload = {"data": {"messages": [{"id": "a"}, "bad", 7]}}
    client, _ = _client([FakeResponse(200, payload)])

    state = client.fetch_preloaded_chat_state("1", "x")

    assert state.messages == [{"id": "a"}]
    assert state.pinned_message is None


def test_fetch_preloaded_chat_state_preserves_current_pin() -> None:
    payload = load_fixture("preloaded_messages_with_pin.json")
    client, _ = _client([FakeResponse(200, payload)])

    state = client.fetch_preloaded_chat_state("1", "x")

    assert [message["id"] for message in state.messages] == ["preloaded-current"]
    assert state.pinned_message is not None
    assert state.pinned_message["message"]["id"] == "startup-pinned-message"


def test_fetch_preloaded_chat_state_missing_messages_is_empty() -> None:
    client, _ = _client([FakeResponse(200, {"data": {}})])

    state = client.fetch_preloaded_chat_state("1", "x")

    assert state.messages == []
    assert state.pinned_message is None


def test_fetch_preloaded_chat_state_does_not_hide_required_response_errors() -> None:
    client, _ = _client([FakeResponse(500, {"err": True})])

    with pytest.raises(KickServerError):
        client.fetch_preloaded_chat_state("1", "x")


def test_fetch_video_metadata_uses_endpoint_specific_not_found_error() -> None:
    client, _ = _client([FakeResponse(404, {})])

    with pytest.raises(KickError, match="video not found"):
        client.fetch_video_metadata("vod-1")


def test_fetch_clip_metadata_uses_clip_endpoint() -> None:
    payload = load_fixture("clip_metadata.json")
    client, session = _client([FakeResponse(200, payload)])

    assert client.fetch_clip_metadata("clip_01M0BHEHDAX2NEAGXG0DA8V9S5") == payload
    assert session.calls == [
        (
            "https://kick.com/api/v2/clips/clip_01M0BHEHDAX2NEAGXG0DA8V9S5",
            {"params": None, "timeout": (10.0, 30.0)},
        )
    ]


def test_fetch_mobile_clip_metadata_uses_anonymous_mobile_endpoint() -> None:
    payload = load_fixture("clip_metadata_mobile.json")
    client, session = _client([FakeResponse(200, payload)])

    assert (
        client.fetch_mobile_clip_metadata("clip_01M0BHEHDAX2NEAGXG0DA8V9S5") == payload
    )
    assert session.calls == [
        (
            "https://mobile.kick.com/api/v1/clips/clip_01M0BHEHDAX2NEAGXG0DA8V9S5",
            {"params": None, "timeout": (10.0, 30.0)},
        )
    ]


def test_fetch_clip_metadata_uses_endpoint_specific_not_found_error() -> None:
    client, _ = _client([FakeResponse(404, {})])

    with pytest.raises(KickError, match="clip not found"):
        client.fetch_clip_metadata("clip_missing")


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


@pytest.mark.parametrize("status", [400, 422])
def test_fetch_forward_message_page_classifies_start_time_rejection(
    status: int,
) -> None:
    client, _ = _client([FakeResponse(status, {"errors": {"start_time": ["invalid"]}})])

    with pytest.raises(api_client.KickForwardHistoryRejected):
        client.fetch_message_page("123", start_time="2026-01-01T00:00:00Z")


@pytest.mark.parametrize("status", [400, 422])
def test_fetch_forward_message_page_preserves_unrelated_client_error(
    status: int,
) -> None:
    client, _ = _client([FakeResponse(status, {"errors": {"channel": ["bad"]}})])

    with pytest.raises(KickError, match=f"unexpected HTTP {status}"):
        client.fetch_message_page("123", start_time="2026-01-01T00:00:00Z")


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(400, [], malformed=False),
        FakeResponse(422, {}, malformed=True),
    ],
)
def test_fetch_forward_message_page_requires_decodable_object_error(
    response: FakeResponse,
) -> None:
    client, _ = _client([response])

    with pytest.raises(KickError, match="unexpected HTTP"):
        client.fetch_message_page("123", start_time="2026-01-01T00:00:00Z")


def test_fetch_reverse_message_page_preserves_terminal_bad_request() -> None:
    client, _ = _client([FakeResponse(400, {})])

    with pytest.raises(KickError, match="unexpected HTTP 400"):
        client.fetch_message_page("123", cursor="older")


def test_fetch_message_page_forbidden_is_challenge() -> None:
    client, _ = _client([FakeResponse(403, {})])

    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_message_page("123")

# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, UserNotFound
from chat_downloader.sites.kick import api_client
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickError, KickServerError
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    load_text_fixture,
)


def _client(
    responses: list[Any],
    *,
    timeout: tuple[float, float] = (10.0, 30.0),
) -> tuple[KickApiClient, FakeKickSession]:
    session = FakeKickSession(responses)
    return KickApiClient(session=session, timeout=timeout), session


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


def test_fetch_clip_metadata_uses_endpoint_specific_not_found_error() -> None:
    client, _ = _client([FakeResponse(404, {})])

    with pytest.raises(KickError, match="clip not found"):
        client.fetch_clip_metadata("clip_missing")


def test_fetch_message_page_passes_non_empty_cursor_only() -> None:
    client, session = _client(
        [
            FakeResponse(200, {"data": {}}),
            FakeResponse(200, {"data": {}}),
        ]
    )

    client.fetch_message_page("123", "next")
    client.fetch_message_page("123", "")

    assert session.calls[0][1]["params"] == {"cursor": "next"}
    assert session.calls[1][1]["params"] is None


def test_fetch_message_page_forbidden_is_challenge() -> None:
    client, _ = _client([FakeResponse(403, {})])

    with pytest.raises(CaptchaChallengeRequired):
        client.fetch_message_page("123")

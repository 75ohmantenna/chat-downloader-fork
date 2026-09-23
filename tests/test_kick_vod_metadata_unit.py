# SPDX-License-Identifier: MIT

"""Current VOD identity fallback, credential isolation, and real composition."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickError, KickVideoNotFound
from chat_downloader.sites.kick.replay_service import get_vod_chat
from chat_downloader.sites.kick.vod_metadata import (
    _resolve_vod_window,
    fetch_vod_metadata,
)
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    message_page,
    raw_message,
    session_patch,
)

VIDEO = "01a09138-ec70-7c4c-a2b7-47a9ed4fc9b4"
REQUEST = ChatRequest(max_attempts=1, interruptible_retry=False)


def test_null_vod_title_falls_back_to_channel_name() -> None:
    data = {
        "livestream": {
            "channel": {"id": 12345},
            "session_title": None,
            "start_time": "2026-08-18T22:20:25+00:00",
            "duration": 1000,
        }
    }
    assert _resolve_vod_window(data, "examplechannel")[2] == "examplechannel"


@pytest.fixture
def fallback_client():
    client = Mock()
    client.fetch_video_metadata.side_effect = KickVideoNotFound("missing")
    client.fetch_channel.return_value = {"id": 12345, "slug": "examplechannel"}
    client.fetch_web_video_metadata.return_value = load_fixture(
        "video_metadata_web.json"
    )
    return client


def test_new_vod_metadata_and_reverse_history_compose_without_alias_lookup():
    web = FakeKickSession([FakeResponse(200, load_fixture("video_metadata_web.json"))])
    primary = FakeKickSession(
        [
            FakeResponse(404, {}),
            FakeResponse(200, {"id": 12345, "slug": "examplechannel"}),
            FakeResponse(
                200,
                message_page(
                    [
                        raw_message(identifier, f"2026-09-11T16:07:{second}Z", content)
                        for identifier, content, second in [
                            ("last", "second", 32),
                            ("first", "first", 31),
                        ]
                    ],
                    cursor=None,
                ),
            ),
        ]
    )
    client = KickApiClient(
        session=primary,
        extra_headers={
            "Authorization": "secret",
            "Cookie": "secret",
            "User-Agent": "custom",
        },
        trust_env=False,
    )
    with session_patch(web) as create:
        chat = get_vod_chat("examplechannel", VIDEO, REQUEST, api_client=client)
    assert chat.id == VIDEO
    assert chat.duration == 26152
    messages = list(chat)
    assert [item["time_in_seconds"] for item in messages] == [29, 30]
    assert messages[0]["time_text"] == "0:29"
    assert create.call_args.kwargs["extra_headers"] == {"User-Agent": "custom"}
    assert (
        web.calls[0][0] == f"https://web.kick.com/api/v1/channels/12345/videos/{VIDEO}"
    )
    assert web.calls[0][1]["headers"] == {"Authorization": None, "Cookie": None}
    assert chat.diagnostics["termination_reason"] == "completed"
    assert chat.diagnostics["transport"]["http_status_counts"] == {"404": 1, "200": 3}
    assert chat.diagnostics["metadata_end_disagreement_seconds"] == 92.0
    client.close()
    assert primary.close_calls == web.close_calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "other"),
        ("channel", {"id": 999, "slug": "examplechannel"}),
        ("status", "private"),
        ("is_live", True),
        ("duration", None),
        ("duration", True),
        ("duration", -1),
        ("duration", float("nan")),
    ],
)
def test_new_metadata_rejects_wrong_identity_state_and_duration(
    fallback_client, field, value
) -> None:
    fallback_client.fetch_web_video_metadata.return_value["data"][field] = value
    with pytest.raises(KickError):
        fetch_vod_metadata(fallback_client, "examplechannel", VIDEO, REQUEST)


@pytest.mark.parametrize(
    ("video", "channel"),
    [
        ("../bad", {}),
        (VIDEO, {"id": "../bad", "slug": "examplechannel"}),
        (VIDEO, {"id": 1, "slug": "different"}),
    ],
)
def test_fallback_validates_endpoint_path_and_channel(fallback_client, video, channel):
    fallback_client.fetch_channel.return_value = channel
    with pytest.raises(KickError):
        fetch_vod_metadata(fallback_client, "examplechannel", video, REQUEST)
    fallback_client.fetch_web_video_metadata.assert_not_called()


def test_web_session_injection_closed_once_and_never_uses_bearer() -> None:
    session = FakeKickSession([FakeResponse(200, {})])
    client = KickApiClient(
        session=session, web_session=session, bearer_token_provider=lambda: "secret"
    )
    client.fetch_web_video_metadata("12345", VIDEO)
    assert "headers" not in session.calls[0][1]
    client.close()
    assert session.close_calls == 1


def test_http_diagnostics_report_transport_failure() -> None:
    client = KickApiClient(session=FakeKickSession([OSError("offline")]))
    with pytest.raises(OSError):
        client.fetch_video_metadata(VIDEO)
    assert client.diagnostics["http_status_counts"] == {"transport_error": 1}


def test_legacy_metadata_checks_declared_video_and_channel_identity() -> None:
    client = Mock()
    client.fetch_video_metadata.return_value = {"uuid": "different"}
    with pytest.raises(KickError, match="different"):
        fetch_vod_metadata(client, "examplechannel", VIDEO, REQUEST)


def test_web_endpoint_rejects_path_injection_and_redirects() -> None:
    client = KickApiClient(session=FakeKickSession([]))
    with pytest.raises(ValueError, match="identity"):
        client.fetch_web_video_metadata("../evil", VIDEO)
    session = FakeKickSession([FakeResponse(302, {})])
    client = KickApiClient(session=session, web_session=session)
    with pytest.raises(KickError, match="302"):
        client.fetch_web_video_metadata("12345", VIDEO)
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("end", "difference"),
    [
        ("2026-09-11T23:24:00Z", 66.0),
        ("2026-09-11T23:22:00Z", -54.0),
        ("2026-09-11T23:22:54Z", None),
        ("bad", None),
        (None, None),
        ("2026-09-11T23:24:00", None),
    ],
)
def test_metadata_explains_disagreement_without_changing_cutoff(
    monkeypatch, fallback_client, end, difference
):
    from chat_downloader.sites.kick import vod_metadata

    payload = fallback_client.fetch_web_video_metadata.return_value
    payload["data"]["end_time"] = end
    logs = []
    diagnostics = {}
    monkeypatch.setattr(vod_metadata, "log", lambda level, text: logs.append(text))
    result = fetch_vod_metadata(
        fallback_client, "examplechannel", VIDEO, REQUEST, diagnostics=diagnostics
    )
    assert result["livestream"]["duration"] == payload["data"]["duration"] * 1000
    assert "trying website metadata" in logs[0]
    assert len(logs) == (2 if difference is not None else 1)
    assert diagnostics == (
        {"metadata_end_disagreement_seconds": difference}
        if difference is not None
        else {}
    )

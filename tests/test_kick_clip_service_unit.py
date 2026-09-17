# SPDX-License-Identifier: MIT

"""Offline coverage for Kick clip replay metadata and composition."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, NoChatReplay
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import clip_service
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickCountryBlocked, KickError
from tests.kick_helpers import FakeKickSession, FakeResponse, load_fixture

CLIP_ID = "clip_01M0BHEHDAX2NEAGXG0DA8V9S5"
VIDEO_ID = "b190fd3b-82e6-4b88-b33c-4b6deae0f968"


def _video_metadata(*, channel_id: int = 1227772) -> dict[str, Any]:
    return {
        "livestream": {
            "session_title": "N3on x Newman Family",
            "start_time": "2026-08-18T22:20:25+00:00",
            "duration": 16_617_000,
            "channel": {"id": channel_id},
        }
    }


def _client() -> Mock:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = _video_metadata()
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}
    return client


def _get_chat(client, *, request=None, clip_id=CLIP_ID):
    return clip_service.get_clip_chat(
        "n3on",
        clip_id,
        request or ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )


def _raw_message(message_id: str, created_at: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": created_at,
        "content": message_id,
        "type": "message",
    }


@pytest.mark.parametrize(
    ("web_status", "timestamps", "cursor"),
    [
        (200, ["22:55:23", "22:55:22", "22:54:23", "22:54:22"], "1787093724000000"),
        (404, ["22:55:21", "22:54:22", "22:54:21", "22:54:20"], "1787093722000000"),
        (500, ["22:55:21", "22:54:22", "22:54:21", "22:54:20"], "1787093722000000"),
    ],
)
def test_clip_replay_composes_metadata_cursor_and_parser(
    web_status, timestamps, cursor
):
    records = [
        _raw_message(message_id, f"2026-08-18T{timestamp}Z")
        for message_id, timestamp in zip(
            ["at-end", "inside", "at-start", "before"], timestamps, strict=True
        )
    ]
    page = {"data": {"messages": records, "cursor": None}}
    web = web_status == 200
    session = FakeKickSession(
        [
            FakeResponse(web_status, load_fixture("clip_metadata.json") if web else {}),
            FakeResponse(
                200,
                _video_metadata() if web else load_fixture("clip_metadata_mobile.json"),
            ),
            FakeResponse(200, page),
        ]
    )
    chat = _get_chat(KickApiClient(session=session, mobile_session=session))
    assert chat.title == "woah"
    assert chat.status == "completed"
    assert chat.video_type == "clip"
    assert chat.id == CLIP_ID
    assert chat.start_time == 0
    assert chat.duration == 60
    assert [message["message_id"] for message in chat] == [
        "at-start",
        "inside",
        "at-end",
    ]
    metadata_url = (
        f"https://kick.com/api/v1/video/{VIDEO_ID}"
        if web
        else f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}"
    )
    assert session.calls == [
        (
            f"https://kick.com/api/v2/clips/{CLIP_ID}",
            {"params": None, "timeout": (10.0, 30.0)},
        ),
        (metadata_url, {"params": None, "timeout": (10.0, 30.0)}),
        (
            "https://kick.com/api/v2/channels/1227772/messages",
            {
                "params": {"cursor": cursor},
                "timeout": (10.0, 30.0),
            },
        ),
    ]


@pytest.mark.parametrize(
    ("mobile", "cursor"),
    [(False, "1787093724000000"), (True, "1787093722000000")],
)
def test_clip_bounds_are_relative_clamped_and_do_not_mutate_request(mobile, cursor):
    client = _client()
    if mobile:
        client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    request = ChatRequest(
        start_time=10, end_time=90, max_attempts=1, interruptible_retry=False
    )
    chat = _get_chat(client, request=request)
    assert request.start_time == 10
    assert request.end_time == 90
    assert chat.start_time == 10
    assert chat.duration == 50
    assert list(chat) == []
    client.fetch_message_page.assert_called_once_with("1227772", cursor=cursor)
    if not mobile:
        client.fetch_mobile_clip_metadata.assert_not_called()


def test_clip_bounds_beyond_duration_yield_no_endpoint_messages() -> None:
    client = _client()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [
                _raw_message("at-end", "2026-08-18T22:55:23Z"),
            ]
        }
    }

    chat = _get_chat(
        client,
        request=ChatRequest(
            start_time=70, end_time=90, max_attempts=1, interruptible_retry=False
        ),
    )

    assert chat.start_time == 60
    assert chat.duration == 0
    assert list(chat) == []
    client.fetch_message_page.assert_not_called()


def test_clip_window_is_truncated_at_source_vod_end() -> None:
    client = _client()
    video = _video_metadata()
    video["livestream"]["duration"] = 2_068_000
    client.fetch_video_metadata.return_value = video

    chat = _get_chat(client)

    assert chat.duration == 30
    assert list(chat) == []


def test_clip_source_channel_mismatch_is_rejected() -> None:
    client = _client()
    client.fetch_video_metadata.return_value = _video_metadata(channel_id=999)

    with pytest.raises(KickError, match="channel does not match"):
        _get_chat(client)

    client.fetch_message_page.assert_not_called()


def test_clip_offset_outside_source_vod_is_rejected() -> None:
    client = _client()
    video = _video_metadata()
    video["livestream"]["duration"] = 1_000
    client.fetch_video_metadata.return_value = video

    with pytest.raises(KickError, match="outside its source VOD"):
        _get_chat(client)


def test_resolve_clip_metadata_accepts_zero_offset() -> None:
    payload = load_fixture("clip_metadata.json")
    payload["clip"]["vod_starts_at"] = 0

    metadata = clip_service._resolve_clip_metadata(payload, CLIP_ID)

    assert metadata.video_id == VIDEO_ID
    assert metadata.channel_id == "1227772"
    assert metadata.start_offset == 0
    assert metadata.duration == 60


def test_resolve_mobile_clip_metadata_normalizes_timestamp() -> None:
    payload = load_fixture("clip_metadata_mobile.json")

    metadata = clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)

    assert metadata.channel_id == "1227772"
    assert metadata.title == "woah"
    assert metadata.started_at.isoformat() == "2026-08-18T22:54:21+00:00"
    assert metadata.duration == 60


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("started_at", "2026-08-18T22:54:21", "invalid started_at"),
        ("started_at", "0001-01-01T00:00:00+01:00", "invalid started_at"),
        *[
            ("started_at", value, "unusable started_at sentinel")
            for value in [
                "0001-01-01T00:00:00Z",
                "0001-01-01T01:00:00+01:00",
                "1970-01-01T00:00:00.000Z",
                "1970-01-01T01:00:00+01:00",
            ]
        ],
        ("started_at", "9999-12-31T23:59:59Z", "unusable time window"),
        ("duration", 181, "180-second duration limit"),
    ],
)
def test_resolve_mobile_clip_metadata_rejects_unusable_window(field, value, message):
    payload = load_fixture("clip_metadata_mobile.json")
    payload["data"][field] = value
    with pytest.raises(KickError, match=message):
        clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing its data object"),
        ({"data": {"id": "clip_other"}}, "returned id"),
        (
            {"data": {"id": CLIP_ID, "channel": {}}},
            "missing a valid channel id",
        ),
        (
            {
                "data": {
                    "id": CLIP_ID,
                    "channel": {"id": 1},
                    "started_at": "not-a-time",
                }
            },
            "invalid started_at",
        ),
    ],
)
def test_resolve_mobile_clip_metadata_rejects_invalid_contract(
    payload: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(KickError, match=message):
        clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)


@pytest.mark.parametrize(
    "changes",
    [
        None,
        {"vod_starts_at": None},
        {"vod_starts_at": -1},
        {"duration": None},
        {"duration": 0},
    ],
)
def test_mobile_clip_fallback_handles_unusable_web_metadata(changes):
    client = _client()
    if changes is None:
        client.fetch_clip_metadata.return_value = {"clip": {"id": CLIP_ID}}
    else:
        client.fetch_clip_metadata.return_value["clip"].update(changes)
    chat = _get_chat(client)
    assert chat.start_time == 0
    assert chat.duration == 60
    assert list(chat) == []
    client.fetch_video_metadata.assert_not_called()


@pytest.mark.parametrize(
    (
        "web_changes",
        "mobile_changes",
        "vod_error",
        "message",
        "cause",
        "cause_text",
        "skip_vod",
    ),
    [
        (
            {"duration": 0},
            {"channel": {"id": 999}},
            False,
            "different channel ids",
            KickError,
            "duration",
            False,
        ),
        (
            {"channel_id": 0, "channel": {}},
            {"duration": 30},
            False,
            "different durations",
            KickError,
            "channel id",
            False,
        ),
        (
            {"vod": {}},
            {"channel": {"id": 999}},
            False,
            "different channel ids",
            NoChatReplay,
            None,
            True,
        ),
        ({}, {"duration": 30}, True, "different durations", KickError, None, False),
        (
            {"vod": {}},
            {"duration": 30},
            False,
            "different durations",
            NoChatReplay,
            None,
            False,
        ),
    ],
)
def test_fallback_reconciles_web_metadata(
    web_changes, mobile_changes, vod_error, message, cause, cause_text, skip_vod
):
    client = _client()
    client.fetch_clip_metadata.return_value["clip"].update(web_changes)
    client.fetch_mobile_clip_metadata.return_value["data"].update(mobile_changes)
    if vod_error:
        client.fetch_video_metadata.side_effect = KickError("VOD unavailable")
    with pytest.raises(KickError, match=message) as captured:
        _get_chat(client)
    assert isinstance(captured.value.__cause__, cause)
    if cause_text:
        assert cause_text in str(captured.value.__cause__)
    if skip_vod:
        client.fetch_video_metadata.assert_not_called()
    client.fetch_message_page.assert_not_called()


def test_web_clip_identity_mismatch_does_not_fall_back() -> None:
    primary = load_fixture("clip_metadata.json")
    primary["clip"]["id"] = "clip_other"
    client = _client()
    client.fetch_clip_metadata.return_value = primary

    with pytest.raises(KickError, match="returned id"):
        _get_chat(client)

    client.fetch_mobile_clip_metadata.assert_not_called()


def test_source_vod_unavailability_falls_back_to_mobile_metadata() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("clip_metadata.json")),
            FakeResponse(400, {}),
            FakeResponse(200, load_fixture("clip_metadata_mobile.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    client = KickApiClient(session=session, mobile_session=session)

    chat = _get_chat(client)

    assert chat.title == "woah"
    assert list(chat) == []
    assert [url for url, _kwargs in session.calls] == [
        f"https://kick.com/api/v2/clips/{CLIP_ID}",
        f"https://kick.com/api/v1/video/{VIDEO_ID}",
        f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
        "https://kick.com/api/v2/channels/1227772/messages",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        *[
            {"duration": value}
            for value in [None, "60000", True, 0, -1, float("nan"), float("inf"), 1e20]
        ],
        *[
            {"start_time": value, "duration": 1_000}
            for value in [
                "9999-12-31T23:59:59Z",
                "0001-01-01T00:00:00+01:00",
            ]
        ],
    ],
)
def test_unusable_source_vod_falls_back_to_mobile_metadata(changes):
    client = _client()
    client.fetch_video_metadata.return_value["livestream"].update(changes)
    chat = _get_chat(client)
    assert chat.duration == 60
    assert list(chat) == []


@pytest.mark.parametrize("endpoint", ["fetch_clip_metadata", "fetch_video_metadata"])
@pytest.mark.parametrize(
    ("error", "message"),
    [(CaptchaChallengeRequired, "blocked"), (KickCountryBlocked, "country blocked")],
)
def test_security_failure_does_not_fall_back_to_mobile(
    endpoint, error, message
) -> None:
    client = _client()
    getattr(client, endpoint).side_effect = error(message)
    with pytest.raises(error, match=message):
        _get_chat(client)
    client.fetch_mobile_clip_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("error", "message"),
    [(KickError, "mobile unavailable"), (CaptchaChallengeRequired, "blocked")],
)
def test_dual_metadata_failure_retains_primary_error_as_cause(error, message):
    client = _client()
    client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    client.fetch_mobile_clip_metadata.side_effect = error(message)
    with pytest.raises(error, match=message) as captured:
        _get_chat(client)
    assert isinstance(captured.value.__cause__, KickError)
    assert str(captured.value.__cause__) == "web unavailable"
    if error is CaptchaChallengeRequired:
        client.fetch_video_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        ({}, KickError, "missing its clip object"),
        (
            {"clip": {"id": "clip_other"}},
            KickError,
            "returned id",
        ),
        (
            {"clip": {"id": CLIP_ID}},
            NoChatReplay,
            "source VOD is unavailable",
        ),
    ],
)
def test_resolve_clip_metadata_rejects_missing_identity_or_replay(
    payload: dict[str, Any],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("vod_starts_at", None, "vod_starts_at"),
        ("vod_starts_at", True, "vod_starts_at"),
        ("vod_starts_at", -1, "vod_starts_at"),
        ("vod_starts_at", float("inf"), "vod_starts_at"),
        ("duration", None, "duration"),
        ("duration", True, "duration"),
        ("duration", 0, "duration"),
        ("duration", float("nan"), "duration"),
    ],
)
def test_resolve_clip_metadata_rejects_invalid_numeric_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = load_fixture("clip_metadata.json")
    payload["clip"][field] = value

    with pytest.raises(KickError, match=message):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_rejects_unsafe_vod_id() -> None:
    payload = load_fixture("clip_metadata.json")
    payload["clip"]["vod"]["id"] = "../channel"

    with pytest.raises(KickError, match="invalid source VOD id"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_rejects_disagreeing_channel_ids() -> None:
    payload = load_fixture("clip_metadata.json")
    payload["clip"]["channel"]["id"] = 999

    with pytest.raises(KickError, match="conflicting channel ids"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_requires_a_numeric_channel_id() -> None:
    payload = load_fixture("clip_metadata.json")
    payload["clip"]["channel_id"] = 0
    payload["clip"]["channel"] = {}

    with pytest.raises(KickError, match="missing a valid channel id"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_get_clip_chat_rejects_unsafe_clip_id_before_request() -> None:
    client = _client()

    with pytest.raises(KickError, match="Invalid Kick clip id"):
        _get_chat(client, clip_id="../clip")

    client.fetch_clip_metadata.assert_not_called()

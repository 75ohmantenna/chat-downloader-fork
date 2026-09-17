# SPDX-License-Identifier: MIT

"""Offline coverage for Kick clip replay metadata and composition."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, NoChatReplay
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import clip_service
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickCountryBlocked, KickError
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    load_fixture,
    message_page,
    raw_message,
)

CLIP_ID = "clip_01M0BHEHDAX2NEAGXG0DA8V9S5"
VIDEO_ID = "b190fd3b-82e6-4b88-b33c-4b6deae0f968"


def _video_metadata(**fields):
    return {
        "livestream": {
            "session_title": "N3on x Newman Family",
            "start_time": "2026-08-18T22:20:25+00:00",
            "duration": 16_617_000,
            "channel": {"id": 1227772},
            **fields,
        }
    }


def _client():
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = _video_metadata()
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = message_page([])
    return client


def _get_chat(client, *, request=None, clip_id=CLIP_ID):
    return clip_service.get_clip_chat(
        "n3on",
        clip_id,
        request or ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )


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
        raw_message(identifier, f"2026-08-18T{timestamp}Z", content=identifier)
        for identifier, timestamp in zip(
            ["at-end", "inside", "at-start", "before"], timestamps, strict=True
        )
    ]
    web = web_status == 200
    session = FakeKickSession(
        [
            FakeResponse(web_status, load_fixture("clip_metadata.json") if web else {}),
            FakeResponse(
                200,
                _video_metadata() if web else load_fixture("clip_metadata_mobile.json"),
            ),
            FakeResponse(200, message_page(records, cursor=None)),
        ]
    )
    chat = _get_chat(KickApiClient(session=session, mobile_session=session))
    assert (chat.title, chat.status, chat.video_type, chat.id) == (
        "woah",
        "completed",
        "clip",
        CLIP_ID,
    )
    assert (chat.start_time, chat.duration) == (0, 60)
    assert [row["message_id"] for row in chat] == ["at-start", "inside", "at-end"]
    metadata_url = (
        f"https://kick.com/api/v1/video/{VIDEO_ID}"
        if web
        else f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}"
    )
    assert session.calls == [
        (url, {"params": params, "timeout": (10.0, 30.0)})
        for url, params in [
            (f"https://kick.com/api/v2/clips/{CLIP_ID}", None),
            (metadata_url, None),
            ("https://kick.com/api/v2/channels/1227772/messages", {"cursor": cursor}),
        ]
    ]


@pytest.mark.parametrize(
    ("mobile", "cursor"),
    [(False, "1787093724000000"), (True, "1787093722000000")],
)
def test_clip_bounds_are_relative_clamped_and_do_not_mutate_request(mobile, cursor):
    client = _client()
    if mobile:
        client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    request = ChatRequest(start_time=10, end_time=90)
    chat = _get_chat(client, request=request)
    assert (request.start_time, request.end_time) == (10, 90)
    assert (chat.start_time, chat.duration) == (10, 50)
    assert list(chat) == []
    client.fetch_message_page.assert_called_once_with("1227772", cursor=cursor)
    if not mobile:
        client.fetch_mobile_clip_metadata.assert_not_called()


def test_clip_bounds_beyond_duration_yield_no_endpoint_messages():
    client = _client()
    client.fetch_message_page.return_value = message_page(
        [raw_message("at-end", "2026-08-18T22:55:23Z", content="at-end")]
    )
    chat = _get_chat(client, request=ChatRequest(start_time=70, end_time=90))
    assert (chat.start_time, chat.duration) == (60, 0)
    assert list(chat) == []
    client.fetch_message_page.assert_not_called()


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"duration": 2_068_000}, None),
        ({"channel": {"id": 999}}, "channel does not match"),
        ({"duration": 1_000}, "outside its source VOD"),
    ],
)
def test_clip_source_vod_window(changes, error):
    client = _client()
    client.fetch_video_metadata.return_value = _video_metadata(**changes)
    if error:
        with pytest.raises(KickError, match=error):
            _get_chat(client)
        if "channel" in changes:
            client.fetch_message_page.assert_not_called()
    else:
        chat = _get_chat(client)
        assert chat.duration == 30
        assert list(chat) == []


def test_resolve_clip_metadata_accepts_zero_offset():
    payload = load_fixture("clip_metadata.json")
    payload["clip"]["vod_starts_at"] = 0
    metadata = clip_service._resolve_clip_metadata(payload, CLIP_ID)
    assert (metadata.video_id, metadata.channel_id) == (VIDEO_ID, "1227772")
    assert (metadata.start_offset, metadata.duration) == (0, 60)


def test_resolve_mobile_clip_metadata_normalizes_timestamp():
    metadata = clip_service._resolve_mobile_clip_metadata(
        load_fixture("clip_metadata_mobile.json"), CLIP_ID
    )
    assert (metadata.channel_id, metadata.title, metadata.duration) == (
        "1227772",
        "woah",
        60,
    )
    assert metadata.started_at.isoformat() == "2026-08-18T22:54:21+00:00"


@pytest.mark.parametrize(
    ("mobile", "changes", "message"),
    [
        *[
            (True, {"started_at": value}, "invalid started_at")
            for value in ["2026-08-18T22:54:21", "0001-01-01T00:00:00+01:00"]
        ],
        *[
            (True, {"started_at": value}, "unusable started_at sentinel")
            for value in [
                "0001-01-01T00:00:00Z",
                "0001-01-01T01:00:00+01:00",
                "1970-01-01T00:00:00.000Z",
                "1970-01-01T01:00:00+01:00",
            ]
        ],
        (True, {"started_at": "9999-12-31T23:59:59Z"}, "unusable time window"),
        (True, {"duration": 181}, "180-second duration limit"),
        *[
            (False, {"vod_starts_at": value}, "vod_starts_at")
            for value in [None, True, -1, float("inf")]
        ],
        *[
            (False, {"duration": value}, "duration")
            for value in [None, True, 0, float("nan")]
        ],
        (False, {"vod": {"id": "../channel"}}, "invalid source VOD id"),
        (False, {"channel": {"id": 999}}, "conflicting channel ids"),
        (False, {"channel_id": 0, "channel": {}}, "missing a valid channel id"),
    ],
)
def test_resolve_clip_metadata_rejects_invalid_fields(mobile, changes, message):
    payload = load_fixture(
        "clip_metadata_mobile.json" if mobile else "clip_metadata.json"
    )
    payload["data" if mobile else "clip"].update(changes)
    resolve = (
        clip_service._resolve_mobile_clip_metadata
        if mobile
        else clip_service._resolve_clip_metadata
    )
    with pytest.raises(KickError, match=message):
        resolve(payload, CLIP_ID)


@pytest.mark.parametrize(
    ("mobile", "payload", "error", "message"),
    [
        (True, {}, KickError, "missing its data object"),
        (True, {"data": {"id": "clip_other"}}, KickError, "returned id"),
        (
            True,
            {"data": {"id": CLIP_ID, "channel": {}}},
            KickError,
            "missing a valid channel id",
        ),
        (
            True,
            {"data": {"id": CLIP_ID, "channel": {"id": 1}, "started_at": "not-a-time"}},
            KickError,
            "invalid started_at",
        ),
        (False, {}, KickError, "missing its clip object"),
        (False, {"clip": {"id": "clip_other"}}, KickError, "returned id"),
        (False, {"clip": {"id": CLIP_ID}}, NoChatReplay, "source VOD is unavailable"),
    ],
)
def test_resolve_clip_metadata_rejects_invalid_contract(
    mobile, payload, error, message
):
    resolve = (
        clip_service._resolve_mobile_clip_metadata
        if mobile
        else clip_service._resolve_clip_metadata
    )
    with pytest.raises(error, match=message):
        resolve(payload, CLIP_ID)


@pytest.mark.parametrize(
    ("endpoint", "changes"),
    [
        ("clip", None),
        *[
            ("clip", {field: value})
            for field, value in [
                ("vod_starts_at", None),
                ("vod_starts_at", -1),
                ("duration", None),
                ("duration", 0),
            ]
        ],
        *[
            ("video", {"duration": value})
            for value in [None, "60000", True, 0, -1, float("nan"), float("inf"), 1e20]
        ],
        *[
            ("video", {"start_time": value, "duration": 1_000})
            for value in ["9999-12-31T23:59:59Z", "0001-01-01T00:00:00+01:00"]
        ],
    ],
)
def test_mobile_clip_fallback_handles_unusable_metadata(endpoint, changes):
    client = _client()
    if changes is None:
        client.fetch_clip_metadata.return_value = {"clip": {"id": CLIP_ID}}
    else:
        payload = getattr(client, f"fetch_{endpoint}_metadata").return_value
        payload["clip" if endpoint == "clip" else "livestream"].update(changes)
    chat = _get_chat(client)
    assert chat.duration == 60
    assert list(chat) == []
    if endpoint == "clip":
        assert chat.start_time == 0
        client.fetch_video_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("web", "mobile", "vod_error", "message", "cause", "cause_text", "skip_vod"),
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
    web, mobile, vod_error, message, cause, cause_text, skip_vod
):
    client = _client()
    client.fetch_clip_metadata.return_value["clip"].update(web)
    client.fetch_mobile_clip_metadata.return_value["data"].update(mobile)
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


def test_web_clip_identity_mismatch_does_not_fall_back():
    client = _client()
    client.fetch_clip_metadata.return_value["clip"]["id"] = "clip_other"
    with pytest.raises(KickError, match="returned id"):
        _get_chat(client)
    client.fetch_mobile_clip_metadata.assert_not_called()


def test_source_vod_unavailability_falls_back_to_mobile_metadata():
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("clip_metadata.json")),
            FakeResponse(400, {}),
            FakeResponse(200, load_fixture("clip_metadata_mobile.json")),
            FakeResponse(200, message_page([])),
        ]
    )
    chat = _get_chat(KickApiClient(session=session, mobile_session=session))
    assert chat.title == "woah"
    assert list(chat) == []
    assert session.requested_urls == [
        f"https://kick.com/api/v2/clips/{CLIP_ID}",
        f"https://kick.com/api/v1/video/{VIDEO_ID}",
        f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
        "https://kick.com/api/v2/channels/1227772/messages",
    ]


@pytest.mark.parametrize("endpoint", ["fetch_clip_metadata", "fetch_video_metadata"])
@pytest.mark.parametrize(
    ("error", "message"),
    [(CaptchaChallengeRequired, "blocked"), (KickCountryBlocked, "country blocked")],
)
def test_security_failure_does_not_fall_back_to_mobile(endpoint, error, message):
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


def test_get_clip_chat_rejects_unsafe_clip_id_before_request():
    client = _client()
    with pytest.raises(KickError, match="Invalid Kick clip id"):
        _get_chat(client, clip_id="../clip")
    client.fetch_clip_metadata.assert_not_called()

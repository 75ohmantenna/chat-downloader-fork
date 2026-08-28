# SPDX-License-Identifier: MIT

"""Offline coverage for Kick clip replay metadata and composition."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import Mock

import pytest

from chat_downloader.errors import CaptchaChallengeRequired, NoChatReplay
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import clip_service
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickError
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


def _raw_message(message_id: str, created_at: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "created_at": created_at,
        "content": message_id,
        "type": "message",
    }


def test_clip_replay_composes_real_client_metadata_cursor_and_parser() -> None:
    page = {
        "data": {
            "messages": [
                _raw_message("at-end", "2026-08-18T22:55:23Z"),
                _raw_message("inside", "2026-08-18T22:55:22Z"),
                _raw_message("at-start", "2026-08-18T22:54:23Z"),
                _raw_message("before", "2026-08-18T22:54:22Z"),
            ],
            "cursor": None,
        }
    }
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("clip_metadata.json")),
            FakeResponse(200, _video_metadata()),
            FakeResponse(200, page),
        ]
    )
    client = KickApiClient(session=session, mobile_session=session)

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

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
    assert session.calls == [
        (
            f"https://kick.com/api/v2/clips/{CLIP_ID}",
            {"params": None, "timeout": (10.0, 30.0)},
        ),
        (
            f"https://kick.com/api/v1/video/{VIDEO_ID}",
            {"params": None, "timeout": (10.0, 30.0)},
        ),
        (
            "https://kick.com/api/v2/channels/1227772/messages",
            {
                "params": {"start_time": "2026-08-18T22:54:23.000000Z"},
                "timeout": (10.0, 30.0),
            },
        ),
    ]


@pytest.mark.parametrize("web_status", [404, 500])
def test_clip_replay_falls_back_to_mobile_metadata_and_absolute_time(
    web_status: int,
) -> None:
    page = {
        "data": {
            "messages": [
                _raw_message("at-end", "2026-08-18T22:55:21Z"),
                _raw_message("inside", "2026-08-18T22:54:22Z"),
                _raw_message("at-start", "2026-08-18T22:54:21Z"),
                _raw_message("before", "2026-08-18T22:54:20Z"),
            ],
            "cursor": None,
        }
    }
    session = FakeKickSession(
        [
            FakeResponse(web_status, {}),
            FakeResponse(200, load_fixture("clip_metadata_mobile.json")),
            FakeResponse(200, page),
        ]
    )
    client = KickApiClient(session=session, mobile_session=session)

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.title == "woah"
    assert chat.start_time == 0
    assert chat.duration == 60
    assert [message["message_id"] for message in chat] == [
        "at-start",
        "inside",
        "at-end",
    ]
    assert session.calls == [
        (
            f"https://kick.com/api/v2/clips/{CLIP_ID}",
            {"params": None, "timeout": (10.0, 30.0)},
        ),
        (
            f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
            {"params": None, "timeout": (10.0, 30.0)},
        ),
        (
            "https://kick.com/api/v2/channels/1227772/messages",
            {
                "params": {"start_time": "2026-08-18T22:54:21.000000Z"},
                "timeout": (10.0, 30.0),
            },
        ),
    ]


def test_clip_bounds_are_relative_clamped_and_do_not_mutate_request() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = _video_metadata()
    client.fetch_message_page.return_value = {"data": {"messages": []}}
    request = ChatRequest(
        start_time=10,
        end_time=90,
        max_attempts=1,
        interruptible_retry=False,
    )

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        request,
        api_client=client,
    )

    assert request.start_time == 10
    assert request.end_time == 90
    assert chat.start_time == 10
    assert chat.duration == 50
    assert list(chat) == []
    client.fetch_message_page.assert_called_once_with(
        "1227772",
        start_time="2026-08-18T22:54:33.000000Z",
    )
    client.fetch_mobile_clip_metadata.assert_not_called()


def test_clip_bounds_beyond_duration_yield_no_endpoint_messages() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = _video_metadata()
    client.fetch_message_page.return_value = {
        "data": {
            "messages": [
                _raw_message("at-end", "2026-08-18T22:55:23Z"),
            ]
        }
    }

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(
            start_time=70,
            end_time=90,
            max_attempts=1,
            interruptible_retry=False,
        ),
        api_client=client,
    )

    assert chat.start_time == 60
    assert chat.duration == 0
    assert list(chat) == []
    client.fetch_message_page.assert_not_called()


def test_clip_window_is_truncated_at_source_vod_end() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    video = _video_metadata()
    video["livestream"]["duration"] = 2_068_000
    client.fetch_video_metadata.return_value = video
    client.fetch_message_page.return_value = {"data": {"messages": []}}

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.duration == 30
    assert list(chat) == []


def test_clip_source_channel_mismatch_is_rejected() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = _video_metadata(channel_id=999)

    with pytest.raises(KickError, match="channel does not match"):
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    client.fetch_message_page.assert_not_called()


def test_clip_offset_outside_source_vod_is_rejected() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    video = _video_metadata()
    video["livestream"]["duration"] = 1_000
    client.fetch_video_metadata.return_value = video

    with pytest.raises(KickError, match="outside its source VOD"):
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )


def test_resolve_clip_metadata_accepts_zero_offset() -> None:
    payload = deepcopy(load_fixture("clip_metadata.json"))
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
    "started_at",
    [
        "2026-08-18T22:54:21",
        "0001-01-01T00:00:00+01:00",
    ],
)
def test_resolve_mobile_clip_metadata_rejects_invalid_absolute_timestamp(
    started_at: str,
) -> None:
    payload = deepcopy(load_fixture("clip_metadata_mobile.json"))
    payload["data"]["started_at"] = started_at

    with pytest.raises(KickError, match="invalid started_at"):
        clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)


@pytest.mark.parametrize(
    "started_at",
    [
        "0001-01-01T00:00:00Z",
        "0001-01-01T01:00:00+01:00",
        "1970-01-01T00:00:00.000Z",
        "1970-01-01T01:00:00+01:00",
    ],
)
def test_resolve_mobile_clip_metadata_rejects_start_sentinel(
    started_at: str,
) -> None:
    payload = deepcopy(load_fixture("clip_metadata_mobile.json"))
    payload["data"]["started_at"] = started_at

    with pytest.raises(KickError, match="unusable started_at sentinel"):
        clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)


def test_resolve_mobile_clip_metadata_rejects_overflowing_window() -> None:
    payload = deepcopy(load_fixture("clip_metadata_mobile.json"))
    payload["data"]["started_at"] = "9999-12-31T23:59:59Z"

    with pytest.raises(KickError, match="unusable time window"):
        clip_service._resolve_mobile_clip_metadata(payload, CLIP_ID)


def test_resolve_mobile_clip_metadata_rejects_overlong_duration() -> None:
    payload = deepcopy(load_fixture("clip_metadata_mobile.json"))
    payload["data"]["duration"] = 181

    with pytest.raises(KickError, match="180-second duration limit"):
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


def test_mobile_clip_fallback_handles_missing_web_vod() -> None:
    primary = {"clip": {"id": CLIP_ID}}
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.start_time == 0
    assert list(chat) == []
    client.fetch_video_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vod_starts_at", None),
        ("vod_starts_at", -1),
        ("duration", None),
        ("duration", 0),
    ],
)
def test_mobile_clip_fallback_handles_invalid_web_replay_field(
    field: str,
    value: object,
) -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"][field] = value
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.duration == 60
    assert list(chat) == []
    client.fetch_video_metadata.assert_not_called()


def test_invalid_web_replay_field_preserves_channel_disagreement() -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"]["duration"] = 0
    mobile = deepcopy(load_fixture("clip_metadata_mobile.json"))
    mobile["data"]["channel"]["id"] = 999
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = mobile

    with pytest.raises(KickError, match="different channel ids") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, KickError)
    assert "duration" in str(captured.value.__cause__)
    client.fetch_message_page.assert_not_called()


def test_missing_web_channel_preserves_duration_for_reconciliation() -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"]["channel_id"] = 0
    primary["clip"]["channel"] = {}
    mobile = deepcopy(load_fixture("clip_metadata_mobile.json"))
    mobile["data"]["duration"] = 30
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = mobile

    with pytest.raises(KickError, match="different durations") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, KickError)
    assert "channel id" in str(captured.value.__cause__)
    client.fetch_message_page.assert_not_called()


def test_mobile_clip_fallback_rejects_web_channel_disagreement() -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"]["vod"] = {}
    mobile = deepcopy(load_fixture("clip_metadata_mobile.json"))
    mobile["data"]["channel"]["id"] = 999
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = mobile

    with pytest.raises(KickError, match="different channel ids") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, NoChatReplay)
    client.fetch_video_metadata.assert_not_called()
    client.fetch_message_page.assert_not_called()


def test_web_clip_identity_mismatch_does_not_fall_back() -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"]["id"] = "clip_other"
    client = Mock()
    client.fetch_clip_metadata.return_value = primary

    with pytest.raises(KickError, match="returned id"):
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    client.fetch_mobile_clip_metadata.assert_not_called()


def test_source_vod_unavailability_falls_back_to_mobile_metadata() -> None:
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("clip_metadata.json")),
            FakeResponse(404, {}),
            FakeResponse(200, load_fixture("clip_metadata_mobile.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    client = KickApiClient(session=session, mobile_session=session)

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.title == "woah"
    assert list(chat) == []
    assert [url for url, _kwargs in session.calls] == [
        f"https://kick.com/api/v2/clips/{CLIP_ID}",
        f"https://kick.com/api/v1/video/{VIDEO_ID}",
        f"https://mobile.kick.com/api/v1/clips/{CLIP_ID}",
        "https://kick.com/api/v2/channels/1227772/messages",
    ]


@pytest.mark.parametrize(
    "duration",
    [None, "60000", True, 0, -1, float("nan"), float("inf"), 1e20],
)
def test_invalid_source_vod_duration_falls_back_to_mobile_metadata(
    duration: object,
) -> None:
    video = _video_metadata()
    video["livestream"]["duration"] = duration
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = video
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.duration == 60
    assert list(chat) == []


@pytest.mark.parametrize(
    "start_time",
    [
        "9999-12-31T23:59:59Z",
        "0001-01-01T00:00:00+01:00",
    ],
)
def test_unusable_source_vod_window_falls_back_to_mobile_metadata(
    start_time: str,
) -> None:
    video = _video_metadata()
    video["livestream"]["start_time"] = start_time
    video["livestream"]["duration"] = 1_000
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.return_value = video
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        ChatRequest(max_attempts=1, interruptible_retry=False),
        api_client=client,
    )

    assert chat.duration == 60
    assert list(chat) == []


def test_source_vod_fallback_rejects_mobile_duration_disagreement() -> None:
    mobile = deepcopy(load_fixture("clip_metadata_mobile.json"))
    mobile["data"]["duration"] = 30
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.side_effect = KickError("VOD unavailable")
    client.fetch_mobile_clip_metadata.return_value = mobile

    with pytest.raises(KickError, match="different durations") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, KickError)
    client.fetch_message_page.assert_not_called()


def test_missing_web_vod_preserves_duration_for_mobile_reconciliation() -> None:
    primary = deepcopy(load_fixture("clip_metadata.json"))
    primary["clip"]["vod"] = {}
    mobile = deepcopy(load_fixture("clip_metadata_mobile.json"))
    mobile["data"]["duration"] = 30
    client = Mock()
    client.fetch_clip_metadata.return_value = primary
    client.fetch_mobile_clip_metadata.return_value = mobile

    with pytest.raises(KickError, match="different durations") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, NoChatReplay)
    client.fetch_message_page.assert_not_called()


def test_source_vod_challenge_does_not_fall_back_to_mobile_endpoint() -> None:
    client = Mock()
    client.fetch_clip_metadata.return_value = load_fixture("clip_metadata.json")
    client.fetch_video_metadata.side_effect = CaptchaChallengeRequired("blocked")

    with pytest.raises(CaptchaChallengeRequired, match="blocked"):
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    client.fetch_mobile_clip_metadata.assert_not_called()


def test_dual_metadata_failure_retains_primary_error_as_cause() -> None:
    client = Mock()
    client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    client.fetch_mobile_clip_metadata.side_effect = KickError("mobile unavailable")

    with pytest.raises(KickError, match="mobile unavailable") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, KickError)
    assert str(captured.value.__cause__) == "web unavailable"


def test_mobile_challenge_is_terminal_and_retains_primary_cause() -> None:
    client = Mock()
    client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    client.fetch_mobile_clip_metadata.side_effect = CaptchaChallengeRequired("blocked")

    with pytest.raises(CaptchaChallengeRequired, match="blocked") as captured:
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    assert isinstance(captured.value.__cause__, KickError)
    client.fetch_video_metadata.assert_not_called()


def test_mobile_clip_bounds_are_relative_and_clamped() -> None:
    client = Mock()
    client.fetch_clip_metadata.side_effect = KickError("web unavailable")
    client.fetch_mobile_clip_metadata.return_value = load_fixture(
        "clip_metadata_mobile.json"
    )
    client.fetch_message_page.return_value = {"data": {"messages": []}}
    request = ChatRequest(
        start_time=10,
        end_time=90,
        max_attempts=1,
        interruptible_retry=False,
    )

    chat = clip_service.get_clip_chat(
        "n3on",
        CLIP_ID,
        request,
        api_client=client,
    )

    assert request.start_time == 10
    assert request.end_time == 90
    assert chat.start_time == 10
    assert chat.duration == 50
    assert list(chat) == []
    client.fetch_message_page.assert_called_once_with(
        "1227772",
        start_time="2026-08-18T22:54:31.000000Z",
    )


def test_clip_challenge_does_not_fall_back_to_mobile_endpoint() -> None:
    client = Mock()
    client.fetch_clip_metadata.side_effect = CaptchaChallengeRequired("blocked")

    with pytest.raises(CaptchaChallengeRequired, match="blocked"):
        clip_service.get_clip_chat(
            "n3on",
            CLIP_ID,
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    client.fetch_mobile_clip_metadata.assert_not_called()


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
    payload = deepcopy(load_fixture("clip_metadata.json"))
    payload["clip"][field] = value

    with pytest.raises(KickError, match=message):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_rejects_unsafe_vod_id() -> None:
    payload = deepcopy(load_fixture("clip_metadata.json"))
    payload["clip"]["vod"]["id"] = "../channel"

    with pytest.raises(KickError, match="invalid source VOD id"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_rejects_disagreeing_channel_ids() -> None:
    payload = deepcopy(load_fixture("clip_metadata.json"))
    payload["clip"]["channel"]["id"] = 999

    with pytest.raises(KickError, match="conflicting channel ids"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_resolve_clip_metadata_requires_a_numeric_channel_id() -> None:
    payload = deepcopy(load_fixture("clip_metadata.json"))
    payload["clip"]["channel_id"] = 0
    payload["clip"]["channel"] = {}

    with pytest.raises(KickError, match="missing a valid channel id"):
        clip_service._resolve_clip_metadata(payload, CLIP_ID)


def test_get_clip_chat_rejects_unsafe_clip_id_before_request() -> None:
    client = Mock()

    with pytest.raises(KickError, match="Invalid Kick clip id"):
        clip_service.get_clip_chat(
            "n3on",
            "../clip",
            ChatRequest(max_attempts=1, interruptible_retry=False),
            api_client=client,
        )

    client.fetch_clip_metadata.assert_not_called()

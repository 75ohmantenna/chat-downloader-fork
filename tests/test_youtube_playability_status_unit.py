# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import (
    ChatDisabled,
    LoginRequired,
    NoChatReplay,
    ParsingError,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.sites.youtube.playability import (
    _build_error_message,
    _raise_for_error_screen,
    _raise_for_popup,
    _raise_for_replay_unavailable,
    is_age_gated,
    is_unplayable,
    raise_if_playability_error,
)
from chat_downloader.sites.youtube.video_status import (
    parse_video_details,
    video_details_to_dict,
)
from chat_downloader.sites.youtube.video_status_helpers import (
    _determine_status,
    _determine_video_type,
    _extract_continuation_info,
)


def test_build_error_message_prefers_simple_text_runs_and_fallbacks() -> None:
    assert (
        _build_error_message(
            {
                "reason": {"simpleText": "Primary reason."},
                "subreason": {"runs": [{"text": "Secondary detail"}]},
            },
            {},
        )
        == "Primary reason. Secondary detail."
    )

    assert (
        _build_error_message(
            {
                "reason": {},
                "subreason": {},
                "itemTitle": "Fallback title.",
                "offerDescription": "Fallback offer.",
            },
            {"reason": "status reason", "subreason": "status subreason"},
        )
        == "Fallback title. Fallback offer."
    )

    assert (
        _build_error_message(
            {"reason": {}, "subreason": {}},
            {"reason": {"unexpected": "shape"}},
        )
        == "{'unexpected': 'shape'}"
    )


def test_playability_rules_detect_age_gate_and_unplayable_status() -> None:
    assert is_age_gated({"playabilityStatus": {"desktopLegacyAgeGateReason": "legacy"}})
    assert is_age_gated(
        {
            "playabilityStatus": {
                "status": "LOGIN_REQUIRED",
                "reason": "Confirm your age",
            },
        },
    )
    assert not is_age_gated({"playabilityStatus": {"status": "OK", "reason": ""}})

    assert is_unplayable({"playabilityStatus": {"status": "UNPLAYABLE"}})
    assert not is_unplayable({"playabilityStatus": {"status": "OK"}})


def test_early_playability_errors() -> None:
    with pytest.raises(VideoUnavailable, match="age-restricted"):
        _raise_for_error_screen(
            {"status": "ERROR"},
            {"playabilityStatus": {"desktopLegacyAgeGateReason": "legacy"}},
        )
    with pytest.raises(VideoUnplayable, match="members only"):
        _raise_for_error_screen(
            {"status": "UNPLAYABLE", "reason": "members only"},
            {"playabilityStatus": {"status": "UNPLAYABLE"}},
        )
    with pytest.raises(VideoUnavailable, match="CAPTCHA verification"):
        _raise_for_error_screen(
            {"status": "ERROR", "errorScreen": {"playerCaptchaViewModel": {}}},
            {"playabilityStatus": {"status": "ERROR"}},
        )


@pytest.mark.parametrize(
    ("status", "reason", "error", "match"),
    [
        ("LOGIN_REQUIRED", "Please sign in.", LoginRequired, "Please sign in"),
        ("LIVE_STREAM_OFFLINE", "Offline.", ChatDisabled, "Offline"),
        (
            "ERROR",
            "This content isn't available, try again later.",
            VideoUnavailable,
            "rate-limited by YouTube",
        ),
        ("SOMETHING_NEW", "Unknown.", VideoUnavailable, "SOMETHING_NEW: Unknown"),
        ("UNPLAYABLE", "Still broken.", VideoUnplayable, "Still broken"),
    ],
)
def test_raise_for_error_screen_maps_playability_statuses(
    status, reason, error, match
) -> None:
    payload = {
        "status": status,
        "errorScreen": {
            "playerErrorMessageRenderer": {"reason": {"simpleText": reason}}
        },
    }
    player_status = "OK" if status == "UNPLAYABLE" else status
    with pytest.raises(error, match=match):
        _raise_for_error_screen(
            payload, {"playabilityStatus": {"status": player_status}}
        )


def _watch_chat(renderer) -> dict:
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "conversationBar": {"liveChatRenderer": renderer},
            },
        },
    }


def _submenu(*items) -> dict:
    selector = {"sortFilterSubMenuRenderer": {"subMenuItems": list(items)}}
    header = {"liveChatHeaderRenderer": {"viewSelector": selector}}
    return _watch_chat({"header": header})


def _reload_item(title: str, token: str) -> dict:
    return {
        "title": title,
        "continuation": {"reloadContinuationData": {"continuation": token}},
    }


def _endpoint_item(title: str, endpoint: str, token: str) -> dict:
    if endpoint == "continuationCommand":
        command = {"continuationCommand": {"token": token}}
    else:
        command = {"getLiveChatEndpoint": {"continuation": token}}
    return {"title": title, "continuationEndpoint": command}


def _availability_message(text: str) -> dict:
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "conversationBar": {
                    "conversationBarRenderer": {
                        "availabilityMessage": {
                            "messageRenderer": {"text": {"runs": [{"text": text}]}}
                        },
                    },
                },
            },
        },
    }


def _popup(title: str, *messages: str) -> dict:
    return {
        "onResponseReceivedActions": [
            {
                "openPopupAction": {
                    "popup": {
                        "confirmDialogRenderer": {
                            "title": {"simpleText": title},
                            "dialogMessages": [{"simpleText": m} for m in messages],
                        },
                    },
                },
            },
        ],
    }


def test_popup_and_replay_unavailable_checks_raise_expected_errors() -> None:
    assert _raise_for_popup({}) is None

    with pytest.raises(VideoUnavailable, match=r"Popup title\. First Second"):
        _raise_for_popup(_popup("Popup title", "First", "Second"))

    with pytest.raises(VideoUnavailable, match="Unable to find initial video contents"):
        _raise_for_replay_unavailable({})

    with pytest.raises(ChatDisabled, match="disabled for this video"):
        _raise_for_replay_unavailable(
            _availability_message("Chat replay is disabled for this video")
        )

    with pytest.raises(NoChatReplay, match="Video does not have a chat replay"):
        _raise_for_replay_unavailable({"contents": {"twoColumnWatchNextResults": {}}})

    # Member-only chats are reported via availabilityMessage; the runtime
    # used to fall through to NoChatReplay. Now classified as VideoUnplayable.
    with pytest.raises(VideoUnplayable, match=r"(?i)members"):
        _raise_for_replay_unavailable(
            _availability_message("This chat is for members only.")
        )


@pytest.mark.parametrize("popup", [False, True])
def test_raise_if_playability_error_prioritizes_popup_over_missing_replay(
    popup,
) -> None:
    initial_data = {"contents": {"twoColumnWatchNextResults": {}}}
    if popup:
        initial_data.update(_popup("Popup title"))
    error = VideoUnavailable if popup else NoChatReplay
    match = "Popup title" if popup else "Video does not have a chat replay"
    with pytest.raises(error, match=match):
        raise_if_playability_error({"playabilityStatus": {}}, initial_data)


def test_video_status_helpers_resolve_types_statuses_and_continuations() -> None:
    assert _determine_video_type(
        {"clipConfig": {"startTimeMs": "2000", "endTimeMs": "7000"}},
        {"isLiveContent": True},
    ) == ("clip", 2.0, 7.0)
    for live, kind in [(False, "premiere"), (True, "video")]:
        assert _determine_video_type({}, {"isLiveContent": live}) == (kind, None, None)

    assert _determine_status({}, {"isLiveNow": True}) == "live"
    not_live = {"isLive": False, "isLiveContent": False}
    assert _determine_status(not_live, {"startTimestamp": "x"}) == "was_live"
    assert _determine_status(not_live, {}) == "not_live"
    assert _determine_status({"isUpcoming": True}, {}) == "upcoming"
    assert _determine_status({"isPostLiveDvr": True}, {}) == "post_live"
    assert _determine_status({"isLiveContent": True}, {}) == "was_live"
    assert _determine_status({}, {}) == "past"

    assert _extract_continuation_info(
        _submenu(
            _reload_item("Top chat", "top-token"),
            _reload_item("Live chat", "live-token"),
        )
    ) == {"Top chat": "top-token", "Live chat": "live-token"}

    assert _extract_continuation_info(
        _submenu(
            _endpoint_item("Live chat", "continuationCommand", "live-endpoint-token"),
            _endpoint_item("Top chat", "getLiveChatEndpoint", "top-endpoint-token"),
        )
    ) == {
        "Live chat": "live-endpoint-token",
        "Top chat": "top-endpoint-token",
    }


def test_parse_video_details_builds_expected_model_and_dict() -> None:
    details = parse_video_details(
        {
            "streamingData": {"adaptiveFormats": [{"approxDurationMs": "10000"}]},
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "startTimestamp": "2024-01-01T00:00:00+00:00",
                        "endTimestamp": "2024-01-01T00:00:10+00:00",
                        "liveBroadcastContent": "live",
                    },
                },
            },
            "videoDetails": {
                "videoId": "abc123",
                "title": "Example title",
                "author": "Example author",
                "channelId": "channel-1",
                "isLive": True,
                "isLiveContent": True,
            },
        },
        _submenu(_reload_item("Live chat", "live-token")),
        "abc123",
    )

    assert details.title == "Example title"
    assert details.author == "Example author"
    assert details.author_id == "channel-1"
    assert details.original_video_id == "abc123"
    assert details.video_type == "video"
    assert details.status == "live"
    assert details.duration == 10.0
    assert details.continuation_info == {"Live chat": "live-token"}
    assert details.start_time is not None
    assert details.end_time is not None
    assert details.end_time > details.start_time

    assert video_details_to_dict(details)["title"] == "Example title"


def test_parse_video_details_raises_for_wrong_video_id_but_allows_clip_override() -> (
    None
):
    player_response = {
        "videoDetails": {
            "videoId": "different-id",
            "title": "Clip title",
            "author": "Author",
            "channelId": "channel-1",
            "isLiveContent": True,
        },
        "clipConfig": {"startTimeMs": "1000", "endTimeMs": "3000"},
    }

    with pytest.raises(ParsingError, match="wrong video"):
        parse_video_details(player_response, {}, "requested-id")

    clip_details = parse_video_details(
        player_response,
        {},
        "requested-id",
        video_type="clip",
    )
    assert clip_details.video_type == "clip"
    assert clip_details.clip_start_time == 1.0
    assert clip_details.clip_end_time == 3.0

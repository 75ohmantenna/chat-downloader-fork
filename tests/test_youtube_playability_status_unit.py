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
from tests.youtube_third_helpers import submenu_item, watch_chat, wrap


def _submenu(*items):
    return watch_chat(
        wrap(
            "header.liveChatHeaderRenderer.viewSelector."
            "sortFilterSubMenuRenderer.subMenuItems",
            list(items),
        )
    )


def _availability_message(text):
    return wrap(
        "contents.twoColumnWatchNextResults.conversationBar."
        "conversationBarRenderer.availabilityMessage.messageRenderer.text.runs",
        [{"text": text}],
    )


def _popup(title, *messages):
    return {
        "onResponseReceivedActions": [
            wrap(
                "openPopupAction.popup.confirmDialogRenderer",
                {
                    "title": {"simpleText": title},
                    "dialogMessages": [{"simpleText": message} for message in messages],
                },
            )
        ]
    }


@pytest.mark.parametrize(
    ("renderer", "status", "expected"),
    [
        (
            {
                "reason": {"simpleText": "Primary reason."},
                "subreason": {"runs": [{"text": "Secondary detail"}]},
            },
            {},
            "Primary reason. Secondary detail.",
        ),
        (
            {
                "reason": {},
                "subreason": {},
                "itemTitle": "Fallback title.",
                "offerDescription": "Fallback offer.",
            },
            {
                "reason": "status reason",
                "subreason": "status subreason",
            },
            "Fallback title. Fallback offer.",
        ),
        (
            {"reason": {}, "subreason": {}},
            {
                "reason": {"unexpected": "shape"},
            },
            "{'unexpected': 'shape'}",
        ),
    ],
)
def test_error_message_precedence_and_fallbacks(renderer, status, expected):
    assert _build_error_message(renderer, status) == expected


@pytest.mark.parametrize(
    ("rule", "status", "expected"),
    [
        (is_age_gated, {"desktopLegacyAgeGateReason": "legacy"}, True),
        (
            is_age_gated,
            {"status": "LOGIN_REQUIRED", "reason": "Confirm your age"},
            True,
        ),
        (is_age_gated, {"status": "OK", "reason": ""}, False),
        (is_unplayable, {"status": "UNPLAYABLE"}, True),
        (is_unplayable, {"status": "OK"}, False),
    ],
)
def test_playability_rules(rule, status, expected):
    assert bool(rule({"playabilityStatus": status})) is expected


@pytest.mark.parametrize(
    ("payload", "player_status", "error", "match"),
    [
        (
            {"status": "ERROR"},
            {"desktopLegacyAgeGateReason": "legacy"},
            VideoUnavailable,
            "age-restricted",
        ),
        (
            {"status": "UNPLAYABLE", "reason": "members only"},
            {"status": "UNPLAYABLE"},
            VideoUnplayable,
            "members only",
        ),
        (
            {"status": "ERROR", "errorScreen": {"playerCaptchaViewModel": {}}},
            {"status": "ERROR"},
            VideoUnavailable,
            "CAPTCHA verification",
        ),
    ],
)
def test_early_playability_errors(payload, player_status, error, match):
    with pytest.raises(error, match=match):
        _raise_for_error_screen(payload, {"playabilityStatus": player_status})


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
def test_raise_for_error_screen_maps_playability_statuses(status, reason, error, match):
    payload = {
        "status": status,
        **wrap("errorScreen.playerErrorMessageRenderer.reason.simpleText", reason),
    }
    player_status = "OK" if status == "UNPLAYABLE" else status
    with pytest.raises(error, match=match):
        _raise_for_error_screen(
            payload, wrap("playabilityStatus.status", player_status)
        )


@pytest.mark.parametrize(
    ("check", "payload", "error", "match"),
    [
        (
            _raise_for_popup,
            _popup("Popup title", "First", "Second"),
            VideoUnavailable,
            r"Popup title\. First Second",
        ),
        (
            _raise_for_replay_unavailable,
            {},
            VideoUnavailable,
            "Unable to find initial video contents",
        ),
        (
            _raise_for_replay_unavailable,
            _availability_message("Chat replay is disabled for this video"),
            ChatDisabled,
            "disabled for this video",
        ),
        (
            _raise_for_replay_unavailable,
            wrap("contents.twoColumnWatchNextResults", {}),
            NoChatReplay,
            "Video does not have a chat replay",
        ),
        (
            _raise_for_replay_unavailable,
            _availability_message("This chat is for members only."),
            VideoUnplayable,
            r"(?i)members",
        ),
    ],
)
def test_popup_and_replay_errors(check, payload, error, match):
    with pytest.raises(error, match=match):
        check(payload)


def test_no_popup_is_accepted():
    assert _raise_for_popup({}) is None


@pytest.mark.parametrize("popup", [False, True])
def test_popup_takes_priority_over_missing_replay(popup):
    initial_data = wrap("contents.twoColumnWatchNextResults", {})
    if popup:
        initial_data.update(_popup("Popup title"))
    error = VideoUnavailable if popup else NoChatReplay
    match = "Popup title" if popup else "Video does not have a chat replay"
    with pytest.raises(error, match=match):
        raise_if_playability_error({"playabilityStatus": {}}, initial_data)


def test_video_status_helpers_resolve_types_statuses_and_continuations():
    assert _determine_video_type(
        {"clipConfig": {"startTimeMs": "2000", "endTimeMs": "7000"}},
        {"isLiveContent": True},
    ) == ("clip", 2.0, 7.0)
    for live, kind in [(False, "premiere"), (True, "video")]:
        assert _determine_video_type({}, {"isLiveContent": live}) == (kind, None, None)
    not_live = {"isLive": False, "isLiveContent": False}
    for video, broadcast, status in [
        ({}, {"isLiveNow": True}, "live"),
        (not_live, {"startTimestamp": "x"}, "was_live"),
        (not_live, {}, "not_live"),
        ({"isUpcoming": True}, {}, "upcoming"),
        ({"isPostLiveDvr": True}, {}, "post_live"),
        ({"isLiveContent": True}, {}, "was_live"),
        ({}, {}, "past"),
    ]:
        assert _determine_status(video, broadcast) == status
    for items, expected in [
        (
            (
                submenu_item("top-token", title="Top chat"),
                submenu_item("live-token", title="Live chat"),
            ),
            {"Top chat": "top-token", "Live chat": "live-token"},
        ),
        (
            (
                submenu_item("live-endpoint-token", "continuationCommand", "Live chat"),
                submenu_item("top-endpoint-token", "getLiveChatEndpoint", "Top chat"),
            ),
            {"Live chat": "live-endpoint-token", "Top chat": "top-endpoint-token"},
        ),
    ]:
        assert _extract_continuation_info(_submenu(*items)) == expected


def test_parse_video_details_builds_expected_model_and_dict():
    details = parse_video_details(
        {
            "streamingData": {"adaptiveFormats": [{"approxDurationMs": "10000"}]},
            "microformat": wrap(
                "playerMicroformatRenderer.liveBroadcastDetails",
                {
                    "startTimestamp": "2024-01-01T00:00:00+00:00",
                    "endTimestamp": "2024-01-01T00:00:10+00:00",
                    "liveBroadcastContent": "live",
                },
            ),
            "videoDetails": {
                "videoId": "abc123",
                "title": "Example title",
                "author": "Example author",
                "channelId": "channel-1",
                "isLive": True,
                "isLiveContent": True,
            },
        },
        _submenu(submenu_item("live-token", title="Live chat")),
        "abc123",
    )
    for key, expected in {
        "title": "Example title",
        "author": "Example author",
        "author_id": "channel-1",
        "original_video_id": "abc123",
        "video_type": "video",
        "status": "live",
        "duration": 10.0,
        "continuation_info": {"Live chat": "live-token"},
    }.items():
        assert getattr(details, key) == expected
    assert details.start_time is not None
    assert details.end_time is not None
    assert details.end_time > details.start_time
    assert video_details_to_dict(details)["title"] == "Example title"


def test_wrong_video_id_requires_clip_override():
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
        player_response, {}, "requested-id", video_type="clip"
    )
    assert clip_details.video_type == "clip"
    assert clip_details.clip_start_time == 1.0
    assert clip_details.clip_end_time == 3.0

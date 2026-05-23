# SPDX-License-Identifier: MIT

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
    assert is_age_gated(
        {"playabilityStatus": {"desktopLegacyAgeGateReason": "legacy"}}
    )
    assert is_age_gated(
        {
            "playabilityStatus": {
                "status": "LOGIN_REQUIRED",
                "reason": "Confirm your age",
            },
        },
    )
    assert not is_age_gated(
        {"playabilityStatus": {"status": "OK", "reason": ""}}
    )

    assert is_unplayable({"playabilityStatus": {"status": "UNPLAYABLE"}})
    assert not is_unplayable({"playabilityStatus": {"status": "OK"}})


def test_raise_for_error_screen_maps_playability_statuses() -> None:
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
            {
                "status": "ERROR",
                "errorScreen": {"playerCaptchaViewModel": {}},
            },
            {"playabilityStatus": {"status": "ERROR"}},
        )

    with pytest.raises(LoginRequired, match="Please sign in"):
        _raise_for_error_screen(
            {
                "status": "LOGIN_REQUIRED",
                "errorScreen": {
                    "playerErrorMessageRenderer": {
                        "reason": {"simpleText": "Please sign in."},
                    },
                },
            },
            {"playabilityStatus": {"status": "LOGIN_REQUIRED"}},
        )

    with pytest.raises(ChatDisabled, match="Offline"):
        _raise_for_error_screen(
            {
                "status": "LIVE_STREAM_OFFLINE",
                "errorScreen": {
                    "playerErrorMessageRenderer": {
                        "reason": {"simpleText": "Offline."}
                    },
                },
            },
            {"playabilityStatus": {"status": "LIVE_STREAM_OFFLINE"}},
        )

    with pytest.raises(VideoUnavailable, match="rate-limited by YouTube"):
        _raise_for_error_screen(
            {
                "status": "ERROR",
                "errorScreen": {
                    "playerErrorMessageRenderer": {
                        "reason": {
                            "simpleText": "This content isn't available, try again later.",
                        },
                    },
                },
            },
            {"playabilityStatus": {"status": "ERROR"}},
        )

    with pytest.raises(VideoUnavailable, match="SOMETHING_NEW: Unknown"):
        _raise_for_error_screen(
            {
                "status": "SOMETHING_NEW",
                "errorScreen": {
                    "playerErrorMessageRenderer": {
                        "reason": {"simpleText": "Unknown."}
                    },
                },
            },
            {"playabilityStatus": {"status": "SOMETHING_NEW"}},
        )

    with pytest.raises(VideoUnplayable, match="Still broken"):
        _raise_for_error_screen(
            {
                "status": "UNPLAYABLE",
                "errorScreen": {
                    "playerErrorMessageRenderer": {
                        "reason": {"simpleText": "Still broken."},
                    },
                },
            },
            {"playabilityStatus": {"status": "OK"}},
        )


def test_popup_and_replay_unavailable_checks_raise_expected_errors() -> None:
    assert _raise_for_popup({}) is None

    with pytest.raises(VideoUnavailable, match=r"Popup title\. First Second"):
        _raise_for_popup(
            {
                "onResponseReceivedActions": [
                    {
                        "openPopupAction": {
                            "popup": {
                                "confirmDialogRenderer": {
                                    "title": {"simpleText": "Popup title"},
                                    "dialogMessages": [
                                        {"simpleText": "First"},
                                        {"simpleText": "Second"},
                                    ],
                                },
                            },
                        },
                    },
                ],
            },
        )

    with pytest.raises(
        VideoUnavailable, match="Unable to find initial video contents"
    ):
        _raise_for_replay_unavailable({})

    with pytest.raises(ChatDisabled, match="disabled for this video"):
        _raise_for_replay_unavailable(
            {
                "contents": {
                    "twoColumnWatchNextResults": {
                        "conversationBar": {
                            "conversationBarRenderer": {
                                "availabilityMessage": {
                                    "messageRenderer": {
                                        "text": {
                                            "runs": [
                                                {
                                                    "text": "Chat replay is disabled for this video",
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        )

    with pytest.raises(NoChatReplay, match="Video does not have a chat replay"):
        _raise_for_replay_unavailable(
            {"contents": {"twoColumnWatchNextResults": {}}}
        )


def test_raise_if_playability_error_delegates_to_popup_and_replay_checks() -> (
    None
):
    with pytest.raises(VideoUnavailable, match="Popup title"):
        raise_if_playability_error(
            {"playabilityStatus": {}},
            {
                "contents": {"twoColumnWatchNextResults": {}},
                "onResponseReceivedActions": [
                    {
                        "openPopupAction": {
                            "popup": {
                                "confirmDialogRenderer": {
                                    "title": {"simpleText": "Popup title"},
                                    "dialogMessages": [],
                                },
                            },
                        },
                    },
                ],
            },
        )

    calls = []

    def record_error_screen(*args):
        calls.append(("error_screen", args))

    def record_popup(*args):
        calls.append(("popup", args))

    def record_replay(*args):
        calls.append(("replay", args))

    import chat_downloader.sites.youtube.playability as mod

    original_error_screen = mod._raise_for_error_screen
    original_popup = mod._raise_for_popup
    original_replay = mod._raise_for_replay_unavailable
    mod._raise_for_error_screen = record_error_screen
    mod._raise_for_popup = record_popup
    mod._raise_for_replay_unavailable = record_replay
    try:
        assert (
            raise_if_playability_error(
                {"playabilityStatus": {}}, {"contents": {}}
            )
            is None
        )
    finally:
        mod._raise_for_error_screen = original_error_screen
        mod._raise_for_popup = original_popup
        mod._raise_for_replay_unavailable = original_replay

    assert [name for name, _args in calls] == [
        "error_screen",
        "popup",
        "replay",
    ]


def test_video_status_helpers_resolve_types_statuses_and_continuations() -> (
    None
):
    assert _determine_video_type(
        {"clipConfig": {"startTimeMs": "2000", "endTimeMs": "7000"}},
        {"isLiveContent": True},
    ) == ("clip", 2.0, 7.0)
    assert _determine_video_type({}, {"isLiveContent": False}) == (
        "premiere",
        None,
        None,
    )
    assert _determine_video_type({}, {"isLiveContent": True}) == (
        "video",
        None,
        None,
    )

    assert _determine_status({}, {"isLiveNow": True}) == "live"
    assert (
        _determine_status(
            {"isLive": False, "isLiveContent": False},
            {"startTimestamp": "x"},
        )
        == "was_live"
    )
    assert (
        _determine_status(
            {"isLive": False, "isLiveContent": False},
            {},
        )
        == "not_live"
    )
    assert _determine_status({"isUpcoming": True}, {}) == "upcoming"
    assert _determine_status({"isPostLiveDvr": True}, {}) == "post_live"
    assert _determine_status({"isLiveContent": True}, {}) == "was_live"
    assert _determine_status({}, {}) == "past"

    assert _extract_continuation_info(
        {
            "contents": {
                "twoColumnWatchNextResults": {
                    "conversationBar": {
                        "liveChatRenderer": {
                            "header": {
                                "liveChatHeaderRenderer": {
                                    "viewSelector": {
                                        "sortFilterSubMenuRenderer": {
                                            "subMenuItems": [
                                                {
                                                    "title": "Top chat",
                                                    "continuation": {
                                                        "reloadContinuationData": {
                                                            "continuation": "top-token",
                                                        },
                                                    },
                                                },
                                                {
                                                    "title": "Live chat",
                                                    "continuation": {
                                                        "reloadContinuationData": {
                                                            "continuation": "live-token",
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    ) == {"Top chat": "top-token", "Live chat": "live-token"}

    assert _extract_continuation_info(
        {
            "contents": {
                "twoColumnWatchNextResults": {
                    "conversationBar": {
                        "liveChatRenderer": {
                            "header": {
                                "liveChatHeaderRenderer": {
                                    "viewSelector": {
                                        "sortFilterSubMenuRenderer": {
                                            "subMenuItems": [
                                                {
                                                    "title": "Live chat",
                                                    "continuationEndpoint": {
                                                        "continuationCommand": {
                                                            "token": "live-endpoint-token",
                                                        },
                                                    },
                                                },
                                                {
                                                    "title": "Top chat",
                                                    "continuationEndpoint": {
                                                        "getLiveChatEndpoint": {
                                                            "continuation": "top-endpoint-token",
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    ) == {
        "Live chat": "live-endpoint-token",
        "Top chat": "top-endpoint-token",
    }


def test_parse_video_details_builds_expected_model_and_dict() -> None:
    details = parse_video_details(
        {
            "streamingData": {
                "adaptiveFormats": [{"approxDurationMs": "10000"}]
            },
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
        {
            "contents": {
                "twoColumnWatchNextResults": {
                    "conversationBar": {
                        "liveChatRenderer": {
                            "header": {
                                "liveChatHeaderRenderer": {
                                    "viewSelector": {
                                        "sortFilterSubMenuRenderer": {
                                            "subMenuItems": [
                                                {
                                                    "title": "Live chat",
                                                    "continuation": {
                                                        "reloadContinuationData": {
                                                            "continuation": "live-token",
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
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

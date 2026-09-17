# SPDX-License-Identifier: MIT

"""Initial YouTube playability profile-fallback regression tests."""

from __future__ import annotations

from typing import Any

import pytest

from chat_downloader.errors import LoginRequired, VideoUnplayable
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.video_initialization import (
    YouTubeVideoInitializationMixin,
)


class _Downloader(YouTubeVideoInitializationMixin):
    def __init__(
        self,
        responses: list[tuple[dict[str, Any], ...]],
        *,
        auto_fallback: bool = True,
        profile: str | None = "youtube_web",
        apply_result: bool = True,
    ) -> None:
        self.responses = responses
        self._auto_profile_fallback = auto_fallback
        self._request_profile = profile
        self.apply_result = apply_result
        self.applied_profiles: list[str] = []
        self.parse_calls = 0

    def _parse_video_data(self, *_args):
        response = self.responses[self.parse_calls]
        self.parse_calls += 1
        return response

    def apply_request_profile(self, profile_name: str) -> bool:
        self.applied_profiles.append(profile_name)
        if self.apply_result:
            self._request_profile = profile_name
        return self.apply_result

    def _session_get(self, *_args, **_kwargs):
        raise AssertionError("profile tests skip chat-page enrichment")


def _response(
    reason: str,
    *,
    continuation: bool = False,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "continuation_info": {"Live chat": "token"} if continuation else {},
            "status": "live",
        },
        {"playabilityStatus": {"status": "UNPLAYABLE", "reason": reason}},
        {"_chat_downloader_continuation_info": True},
        {"profile": reason},
    )


def _request() -> ChatRequest:
    return ChatRequest(url="https://www.youtube.com/watch?v=LLpNUqHVam8")


@pytest.mark.parametrize(
    ("reasons", "options", "profiles", "match"),
    [
        (
            [
                "Video unavailable",
                "The uploader has not made this video available in your country",
            ],
            {},
            ["youtube_android"],
            "available in your country",
        ),
        (["Video unavailable"], {"auto_fallback": False}, [], "Video unavailable"),
        (
            ["The uploader has not made this video available in your country"],
            {},
            [],
            "available in your country",
        ),
        (
            ["Video unavailable"] * 3,
            {},
            ["youtube_android", "youtube_ios"],
            "Video unavailable",
        ),
        (
            ["Video unavailable"],
            {"apply_result": False},
            ["youtube_android"],
            "Video unavailable",
        ),
    ],
)
def test_initial_profile_fallback_failure_policy(
    reasons, options, profiles, match
) -> None:
    downloader = _Downloader([_response(reason) for reason in reasons], **options)
    with pytest.raises(VideoUnplayable, match=match):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())
    assert downloader.applied_profiles == profiles
    assert downloader.parse_calls == len(reasons)


def test_initial_profile_fallback_can_recover_chat_continuation() -> None:
    downloader = _Downloader(
        [
            _response("Video unavailable"),
            _response("", continuation=True),
        ]
    )

    details, ytcfg = downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert details["continuation_info"] == {"Live chat": "token"}
    assert ytcfg == {"profile": ""}
    assert downloader.applied_profiles == ["youtube_android"]


def test_initial_profile_fallback_does_not_rotate_login_required() -> None:
    details, player, data, _ = _response("Please sign in")
    player["playabilityStatus"].update(
        {
            "status": "LOGIN_REQUIRED",
            "errorScreen": {
                "playerErrorMessageRenderer": {
                    "reason": {"simpleText": "Please sign in"},
                }
            },
        }
    )
    downloader = _Downloader([(details, player, data, {})])

    with pytest.raises(LoginRequired, match="Please sign in"):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == []
    assert downloader.parse_calls == 1

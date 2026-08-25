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


def _details(continuation: bool = False) -> dict[str, Any]:
    return {
        "continuation_info": {"Live chat": "token"} if continuation else {},
        "status": "live",
    }


def _player(reason: str) -> dict[str, Any]:
    return {
        "playabilityStatus": {
            "status": "UNPLAYABLE",
            "reason": reason,
        }
    }


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
        _details(continuation),
        _player(reason),
        {"_chat_downloader_continuation_info": True},
        {"profile": reason},
    )


def _request() -> ChatRequest:
    return ChatRequest(url="https://www.youtube.com/watch?v=LLpNUqHVam8")


def test_initial_profile_fallback_surfaces_more_specific_error() -> None:
    downloader = _Downloader(
        [
            _response("Video unavailable"),
            _response("The uploader has not made this video available in your country"),
        ]
    )

    with pytest.raises(VideoUnplayable, match="available in your country"):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == ["youtube_android"]
    assert downloader.parse_calls == 2


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


@pytest.mark.parametrize(
    ("auto_fallback", "reason"),
    [
        (False, "Video unavailable"),
        (True, "The uploader has not made this video available in your country"),
    ],
)
def test_initial_profile_fallback_skips_disabled_or_specific_failures(
    auto_fallback: bool,
    reason: str,
) -> None:
    downloader = _Downloader(
        [_response(reason)],
        auto_fallback=auto_fallback,
    )

    with pytest.raises(VideoUnplayable, match=reason):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == []
    assert downloader.parse_calls == 1


def test_initial_profile_fallback_exhausts_each_profile_once() -> None:
    downloader = _Downloader(
        [_response("Video unavailable") for _ in range(3)],
    )

    with pytest.raises(VideoUnplayable, match="Video unavailable"):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == ["youtube_android", "youtube_ios"]
    assert downloader.parse_calls == 3


def test_initial_profile_fallback_stops_when_profile_cannot_be_applied() -> None:
    downloader = _Downloader(
        [_response("Video unavailable")],
        apply_result=False,
    )

    with pytest.raises(VideoUnplayable, match="Video unavailable"):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == ["youtube_android"]
    assert downloader.parse_calls == 1


def test_initial_profile_fallback_does_not_rotate_login_required() -> None:
    downloader = _Downloader(
        [
            (
                _details(),
                {
                    "playabilityStatus": {
                        "status": "LOGIN_REQUIRED",
                        "reason": "Please sign in",
                        "errorScreen": {
                            "playerErrorMessageRenderer": {
                                "reason": {"simpleText": "Please sign in"}
                            }
                        },
                    }
                },
                {"_chat_downloader_continuation_info": True},
                {},
            )
        ]
    )

    with pytest.raises(LoginRequired, match="Please sign in"):
        downloader._get_initial_video_info("LLpNUqHVam8", _request())

    assert downloader.applied_profiles == []
    assert downloader.parse_calls == 1

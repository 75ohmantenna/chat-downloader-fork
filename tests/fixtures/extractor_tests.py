# SPDX-License-Identifier: MIT

"""Cross-site integration scenarios kept out of the runtime package."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import (
    NoChatReplay,
    UserNotFound,
    VideoUnavailable,
)
from tests.fixtures.replay_checks import assert_message_limit, assert_timed_window

if TYPE_CHECKING:
    from chat_downloader.sites.models import Chat


_TWITCH_CLIP_ID = "TrappedFrigidPenguinSeemsGood"
_TWITCH_CLIP_END = 45


def _assert_twitch_clip(chat: Chat, messages: list[dict[str, Any]]) -> None:
    assert chat.id == _TWITCH_CLIP_ID
    assert chat.video_type == "clip"
    window = chat._request_window
    assert window is not None
    assert window["start_time"] == 0
    basis = window["time_basis"]
    assert basis in {"clip", "recording"}
    end = float(_TWITCH_CLIP_END)
    # Clips without a provider VOD offset use the requested recording window.
    if basis == "clip" and chat.duration is not None:
        end = min(end, chat.duration)
    assert window["end_time"] == end
    assert_timed_window(messages, start=0, end=end, required_types=("text_message",))


def _has_twitch_channel_metadata(chat: Chat) -> bool:
    return (
        chat.id == "xenova"
        and bool(chat.title and chat.title.strip())
        and chat.status in {"live", "upcoming"}
        and chat.video_type == "video"
    )


BASE_EXTRACTOR_TESTS: list[dict[str, Any]] = [
    {
        "name": "Discover Twitch channel metadata using the bare hostname.",
        "network_live": True,
        "params": {
            "url": "https://twitch.tv/xenova",
            "timeout": 5,
        },
        "expected_result": {
            "chat_condition": _has_twitch_channel_metadata,
        },
    },
    {
        "name": "Get a limited number of messages from a YouTube replay.",
        "params": {
            "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
            "max_messages": 10,
            "timeout": 60,
        },
        "expected_result": {
            "after_capture": partial(assert_message_limit, limit=10),
        },
    },
    {
        "name": "Scheme not supplied",
        "params": {
            "url": "www.youtube.com/watch?v=wXspodtIxYU",
            "max_messages": 10,
            "timeout": 60,
        },
        "expected_result": {
            "after_capture": partial(assert_message_limit, limit=10),
        },
    },
]

TWITCH_EXTRACTOR_TESTS: list[dict[str, Any]] = [
    {
        "name": "Discover livestream metadata.",
        "network_live": True,
        "params": {"url": "https://www.twitch.tv/xenova", "timeout": 5},
        "expected_result": {
            "chat_condition": _has_twitch_channel_metadata,
        },
    },
    {
        "name": "Deliver chat messages from a livestream.",
        "network_live": True,
        "requires_env": "TWITCH_TEST_CHANNEL",
        "params": {
            "url": "https://www.twitch.tv/{channel}",
            "timeout": 30,
        },
        "expected_result": {
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Past broadcast with chat replay.",
        "params": {
            "url": "https://www.twitch.tv/videos/87136772",
            "max_messages": 30,
        },
        "expected_result": {
            "after_capture": partial(assert_message_limit, limit=30),
            "messages_condition": lambda messages: any(
                message["message_type"] == "text_message" for message in messages
            ),
        },
    },
    {
        "name": "Clip with chat replay.",
        "params": {
            "url": f"https://clips.twitch.tv/{_TWITCH_CLIP_ID}",
            "start_time": 0,
            "end_time": _TWITCH_CLIP_END,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "after_capture": _assert_twitch_clip,
        },
    },
    {
        "name": (
            "This clip's past broadcast has expired and chat replay is no longer "
            "available."
        ),
        "params": {
            "url": "https://clips.twitch.tv/AverageSparklyTortoisePeoplesChamp",
        },
        "expected_result": {"error": NoChatReplay},
    },
    {
        "name": "Sorry. Unless you've got a time machine, that content is unavailable.",
        "params": {"url": "https://www.twitch.tv/videos/1"},
        "expected_result": {"error": VideoUnavailable},
    },
]

KICK_EXTRACTOR_TESTS: list[dict[str, Any]] = [
    {
        "name": "Missing Kick channel raises UserNotFound.",
        "network_live": True,
        "params": {"url": "https://kick.com/somelikelyofflinechannel"},
        "expected_result": {"error": UserNotFound},
    },
]

# SPDX-License-Identifier: MIT

"""Cross-site integration scenarios kept out of the runtime package."""

from __future__ import annotations

from typing import Any

from chat_downloader.errors import (
    NoChatReplay,
    UserNotFound,
    VideoUnavailable,
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
            "chat_condition": lambda chat: bool(chat.id and chat.title),
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
            "messages_condition": lambda messages: 0 < len(messages) <= 10,
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
            "messages_condition": lambda messages: 0 < len(messages) <= 10,
        },
    },
]

TWITCH_EXTRACTOR_TESTS: list[dict[str, Any]] = [
    {
        "name": "Discover livestream metadata.",
        "network_live": True,
        "params": {"url": "https://www.twitch.tv/xenova", "timeout": 5},
        "expected_result": {
            "chat_condition": lambda chat: bool(chat.id and chat.title),
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
            "messages_condition": lambda messages: (
                len(messages) <= 30
                and any(m.get("message_type") == "text_message" for m in messages)
            ),
        },
    },
    {
        "name": "Clip with chat replay.",
        "params": {
            "url": "https://clips.twitch.tv/TrappedFrigidPenguinSeemsGood",
        },
        "expected_result": {
            "message_types": ["text_message"],
            "messages_condition": lambda messages: len(messages) > 0,
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

# SPDX-License-Identifier: MIT

"""Regression checks for live replay scenario assertions."""

from __future__ import annotations

from typing import Any

import pytest

from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from tests.fixtures.extractor_tests import _TWITCH_CLIP_ID, _assert_twitch_clip
from tests.fixtures.youtube.extractor_tests import (
    _PAST_CLIP_ID,
    _assert_replay_events,
    _assert_youtube_clip,
)


def test_event_witness_must_have_a_replay_offset() -> None:
    chat = Chat()
    messages: list[dict[str, Any]] = [
        {"message_type": "text_message", "time_in_seconds": 1},
        {"message_type": "paid_message", "action_type": "add_chat_item"},
    ]
    with pytest.raises(AssertionError, match="Missing timed replay events"):
        _assert_replay_events(
            chat,
            messages,
            start=0,
            end=20,
            witnesses=(("paid_message", "add_chat_item"),),
        )


def test_paid_filter_rejects_ordinary_chat_if_group_is_widened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_MESSAGE_GROUPS, "superchat", ["paid_message", "text_message"])
    messages: list[dict[str, Any]] = [
        {
            "message_type": "paid_message",
            "action_type": "add_chat_item",
            "time_in_seconds": 0,
        },
        {"message_type": "text_message", "time_in_seconds": 1},
    ]
    with pytest.raises(AssertionError, match="Paid filters admitted ordinary chat"):
        _assert_replay_events(
            Chat(),
            messages,
            start=0,
            end=20,
            groups=("superchat",),
            witnesses=(("paid_message", "add_chat_item"),),
        )


@pytest.mark.parametrize(
    ("time_basis", "duration", "end"),
    [("clip", 30, 30), ("recording", 30, 45)],
)
def test_twitch_clip_checks_the_effective_window(
    time_basis: str, duration: float, end: float
) -> None:
    chat = Chat(id=_TWITCH_CLIP_ID, video_type="clip", duration=duration)
    chat._request_window = {"start_time": 0, "end_time": end, "time_basis": time_basis}
    messages: list[dict[str, Any]] = [
        {"message_type": "text_message", "time_in_seconds": end},
    ]
    _assert_twitch_clip(chat, messages)
    messages[0]["time_in_seconds"] = end + 1
    with pytest.raises(AssertionError, match="outside the requested window"):
        _assert_twitch_clip(chat, messages)


def test_youtube_clip_checks_offsets_in_clip_time() -> None:
    chat = Chat(id=_PAST_CLIP_ID, video_type="clip", duration=1000)
    chat._request_window = {
        "start_time": 500,
        "end_time": 510,
        "time_basis": "recording",
    }
    messages: list[dict[str, Any]] = [
        {"message_type": "text_message", "time_in_seconds": 4},
        {"message_type": "viewer_engagement_message"},
    ]
    _assert_youtube_clip(chat, messages, clip_id=_PAST_CLIP_ID)

    messages.append({"message_type": "text_message"})
    with pytest.raises(AssertionError, match="text_message is missing its offset"):
        _assert_youtube_clip(chat, messages, clip_id=_PAST_CLIP_ID)
    messages[-1]["time_in_seconds"] = 504
    with pytest.raises(AssertionError, match="outside the requested window"):
        _assert_youtube_clip(chat, messages, clip_id=_PAST_CLIP_ID)
    messages[-1]["time_in_seconds"] = 4
    messages.append(messages[0])
    with pytest.raises(AssertionError, match="exceeded the clip message limit"):
        _assert_youtube_clip(chat, messages, clip_id=_PAST_CLIP_ID)

# SPDX-License-Identifier: MIT

"""Network test scenarios for the YouTube extractor."""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import (
    ChatDisabled,
    LoginRequired,
    NoChatReplay,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from tests.fixtures.replay_checks import assert_live_delivery, assert_timed_window

if TYPE_CHECKING:
    from chat_downloader.sites.models import Chat


_YOUTUBE_CLIP_END = 10
_YOUTUBE_CLIP_LIMIT = 3
_PAST_CLIP_ID = "Ugy_1IfsnZUWZSXL6C94AaABCQ"
_PREMIERE_CLIP_ID = "UgzNZCNnPzq-M3_Utjl4AaABCQ"
_GIFT_START = 5 * 3600 + 22 * 60 + 17
_GIFT_END = _GIFT_START + 11


def _assert_top_chat(chat: Chat, messages: list[dict[str, Any]]) -> None:
    assert chat.diagnostics.get("chat_view") == "Top chat replay"
    assert_timed_window(messages, start=0, end=20, required_types=("text_message",))


def _assert_replay_events(
    _chat: Chat,
    messages: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    groups: tuple[str, ...] = (),
    witnesses: tuple[tuple[str, str], ...],
) -> None:
    assert_timed_window(messages, start=start, end=end)
    types = {message["message_type"] for message in messages}
    if groups:
        allowed = {kind for group in groups for kind in _MESSAGE_GROUPS[group]}
        assert types <= allowed, f"Unexpected message types: {types - allowed}"
        assert "text_message" not in types, "Paid filters admitted ordinary chat."
    delivered = {
        (message["message_type"], message.get("action_type"))
        for message in messages
        if "time_in_seconds" in message
    }
    missing = set(witnesses) - delivered
    assert not missing, f"Missing timed replay events: {sorted(missing)}"


def _assert_youtube_clip(
    chat: Chat, messages: list[dict[str, Any]], *, clip_id: str
) -> None:
    assert chat.id == clip_id
    assert chat.video_type == "clip"
    assert len(messages) <= _YOUTUBE_CLIP_LIMIT, (
        f"Replay exceeded the clip message limit of {_YOUTUBE_CLIP_LIMIT}."
    )
    window = chat._request_window
    assert window is not None
    # The request window uses recording offsets, while emitted offsets are local
    # to the clip. chat.duration may describe the full recording.
    assert window["time_basis"] == "recording"
    start = window["start_time"]
    end = window["end_time"]
    assert isinstance(start, (int, float))
    assert isinstance(end, (int, float))
    assert 0 <= start < end
    assert end == start + _YOUTUBE_CLIP_END
    assert_timed_window(
        messages, start=0, end=_YOUTUBE_CLIP_END, required_types=("text_message",)
    )


def _has_resolved_livestream_metadata(chat: Chat) -> bool:
    return (
        not chat._metadata_pending
        and bool(chat.id and re.fullmatch(r"[0-9A-Za-z_-]{11}", chat.id))
        and bool(chat.title and chat.title.strip())
        and chat.status in {"live", "upcoming"}
    )


def _assert_youtube_live_delivery(chat: Chat, messages: list[dict[str, Any]]) -> None:
    assert_live_delivery(chat, messages, allowed_types=_MESSAGE_GROUPS["messages"])
    assert chat.status in {"live", "upcoming"}, (
        f"Expected live or upcoming YouTube chat, received {chat.status!r}."
    )


YOUTUBE_EXTRACTOR_TESTS = [
    # Get top live streams
    # https://www.youtube.com/results?search_query&sp=CAMSAkAB
    # OTHER:
    # Japanese characters and lots of superchats
    # https://www.youtube.com/watch?v=UlemRwXYWHg
    # strange end times:
    # https://www.youtube.com/watch?v=DzEbfQI4TPQ
    # https://www.youtube.com/watch?v=7PPnCOhkxqo
    # purchased a product linked to the YouTube channel merchandising
    # https://youtu.be/y5ih7nqEoc4
    # TESTING FOR CORRECT FUNCTIONALITY
    {
        "name": "Discover livestream metadata using channel id.",
        "scope": "live",
        "params": {
            "url": "https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow",
            "timeout": 20,
            "max_messages": 1,
        },
        "expected_result": {
            "chat_condition": _has_resolved_livestream_metadata,
        },
    },
    {
        "name": "Discover livestream metadata using custom url (1).",
        "scope": "live",
        "params": {
            "url": "https://www.youtube.com/c/lofigirl",
            "timeout": 20,
            "max_messages": 1,
        },
        "expected_result": {
            "chat_condition": _has_resolved_livestream_metadata,
        },
    },
    {
        "name": "Discover livestream metadata using custom url (2).",
        "scope": "live",
        "params": {
            "url": "https://www.youtube.com/lofigirl",
            "timeout": 20,
            "max_messages": 1,
        },
        "expected_result": {
            "chat_condition": _has_resolved_livestream_metadata,
        },
    },
    {
        "name": "Discover livestream metadata using user id.",
        "scope": "live",
        "params": {
            "url": "https://www.youtube.com/user/YellowBrickCinema",
            "timeout": 20,
            "max_messages": 1,
        },
        "expected_result": {
            "chat_condition": _has_resolved_livestream_metadata,
        },
    },
    {
        "name": "Deliver chat messages from a discovered livestream.",
        "scope": "live",
        "requires_env": "YOUTUBE_TEST_CHANNEL_ID",
        "params": {
            "url": "https://www.youtube.com/channel/{channel}",
            "timeout": 30,
            "max_messages": 1,
        },
        "expected_result": {
            "after_capture": _assert_youtube_live_delivery,
        },
    },
    {
        "name": "Get top chat messages from live chat replay",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
            "start_time": 0,
            "end_time": 20,
            "chat_type": "top",
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "after_capture": _assert_top_chat,
        },
    },
    {
        "name": "Get superchat and ticker messages from live chat replay",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=UlemRwXYWHg",
            "start_time": 0,
            "end_time": 20,
            "message_groups": ["superchat", "tickers"],
        },
        "expected_result": {
            "after_capture": partial(
                _assert_replay_events,
                start=0,
                end=20,
                groups=("superchat", "tickers"),
                witnesses=(
                    ("paid_message", "add_chat_item"),
                    ("ticker_paid_message_item", "add_live_chat_ticker_item"),
                ),
            ),
        },
    },
    {
        "name": "Get all messages from live chat replay",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=97w16cYskVI",
            "start_time": 0,
            "end_time": 50,
            "message_types": ["all"],
            "youtube_replay_poll_interval": 1.0,
        },
        "expected_result": {
            "after_capture": partial(
                _assert_replay_events,
                start=0,
                end=50,
                witnesses=(
                    ("text_message", "add_chat_item"),
                    ("paid_message", "add_chat_item"),
                    ("ticker_paid_message_item", "add_live_chat_ticker_item"),
                ),
            ),
        },
    },
    {
        "name": "Get messages from a premiere",  # Premiere
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=zVCs9Cug_qM",
            "max_messages": 3,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Chat replay with Super Chat messages",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=UlemRwXYWHg",
            "start_time": 0,
            "end_time": 20,
            "message_groups": ["superchat"],
        },
        "expected_result": {
            "after_capture": partial(
                _assert_replay_events,
                start=0,
                end=20,
                groups=("superchat",),
                witnesses=(("paid_message", "add_chat_item"),),
            ),
        },
    },
    {
        "name": "Chat replay with membership gifts",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=cb0h-KbpDo8",
            "start_time": _GIFT_START,
            "end_time": _GIFT_END,
            "message_groups": ["all"],
        },
        "expected_result": {
            "after_capture": partial(
                _assert_replay_events,
                start=_GIFT_START,
                end=_GIFT_END,
                witnesses=(
                    ("sponsorships_gift_purchase_announcement", "add_chat_item"),
                    ("ticker_sponsor_item", "add_live_chat_ticker_item"),
                ),
            ),
        },
    },
    {
        "name": "Get chat messages from an unplayable stream.",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=V2Afni3S-ok",
            "start_time": 10,
            "end_time": 100,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    # TESTING FOR ERRORS
    {
        "name": "Video does not exist",
        "scope": "replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=xxxxxxxxxxx",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": (
            "This video is no longer available due to a copyright claim by "
            "International Olympic Committee."
        ),
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=cjk2UKkzY0g",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "YouTube Premium video is unavailable.",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=i1Ko8UG-Tdo",
        },
        "expected_result": {
            "error": VideoUnplayable,
        },
    },
    {
        "name": "Rental video preview is unavailable.",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=yYr8q0y5Jfg",
        },
        "expected_result": {
            "error": VideoUnplayable,
        },
    },
    {
        # The following content has been identified by the YouTube community
        # as inappropriate or offensive to some audiences.
        "name": (
            "This video has been removed for violating YouTube's policy on "
            "hate speech. Learn more about combating hate speech in your "
            "country."
        ),
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=6SJNVb0GnPI",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "Former members-only content with replay chat",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=vprErlL1w2E",
            "max_messages": 3,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: 0 < len(messages) <= 3,
        },
    },
    {
        "name": "Chat is disabled for this live stream",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=XWq5kBlakcQ",
        },
        "expected_result": {
            "error": [ChatDisabled, LoginRequired],
        },
    },
    {
        "name": "Live chat replay has been turned off for this video",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=7lGZvbasx6A",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        "name": "Video is private",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=ijFMXqa-N0c",
        },
        "expected_result": {
            "error": LoginRequired,
        },
    },
    {
        "name": "This live stream recording is not available.",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=68kru9DqUS4",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        # Age restricted -- YouTube may return LoginRequired or VideoUnavailable
        # depending on whether the age-gate check fires first.
        "name": (
            "Sign in to confirm your age. This video may be inappropriate "
            "for some users."
        ),
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=WaOKSUlf4TM",
        },
        "expected_result": {
            "error": [LoginRequired, VideoUnavailable],
        },
    },
    {
        # Age restricted -- YouTube may return LoginRequired or VideoUnavailable
        # depending on whether the age-gate check fires first.
        "name": (
            "Sign in to confirm your age. This video may be inappropriate "
            "for some users (secondary watch page)."
        ),
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/watch?v=_8W6Aoql-yk",
        },
        "expected_result": {
            "error": [LoginRequired, VideoUnavailable],
        },
    },
    # Clips
    {
        "name": "Chat replay of clip (past broadcast)",
        "scope": "replay",
        "params": {
            "url": f"https://www.youtube.com/clip/{_PAST_CLIP_ID}",
            "start_time": 0,
            "end_time": _YOUTUBE_CLIP_END,
            "max_messages": _YOUTUBE_CLIP_LIMIT,
        },
        "expected_result": {
            "after_capture": partial(_assert_youtube_clip, clip_id=_PAST_CLIP_ID),
        },
    },
    {
        "name": "Chat replay of clip (premiere)",
        "scope": "replay",
        "params": {
            "url": f"https://www.youtube.com/clip/{_PREMIERE_CLIP_ID}",
            "start_time": 0,
            "end_time": _YOUTUBE_CLIP_END,
            "max_messages": _YOUTUBE_CLIP_LIMIT,
        },
        "expected_result": {
            "after_capture": partial(_assert_youtube_clip, clip_id=_PREMIERE_CLIP_ID),
        },
    },
    {
        "name": "Clip does not have a chat replay.",
        "scope": "environment",
        "params": {
            "url": "https://www.youtube.com/clip/UgwVu73xQ5FUiGnteZJ4AaABCQ",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        "name": (
            "Clip not available. The clip can be unavailable if it was "
            "deleted, or if the video it's based on was removed or edited."
        ),
        "scope": "environment",
        "params": {
            "url": "https://youtube.com/clip/UgxJiPo-4EeSYDfrYp94AaABCQ",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "Clip does not exist",
        "scope": "replay",
        "params": {
            "url": "https://youtube.com/clip/x",
        },
        "expected_result": {
            "error": VideoNotFound,
        },
    },
]

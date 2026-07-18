# SPDX-License-Identifier: MIT

"""Network test scenarios for the YouTube extractor."""

from __future__ import annotations

from chat_downloader.errors import (
    ChatDisabled,
    LoginRequired,
    NoChatReplay,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
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
        "name": "Get chat messages from known replay video URL",
        "params": {
            "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
            "max_messages": 10,
        },
    },
    {
        "name": ("Get chat messages from livestream, using channel id."),
        "params": {
            "url": "https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow",
            "timeout": 5,
        },
    },
    {
        "name": "Get chat messages from livestream, using custom url (1).",
        "params": {"url": "https://www.youtube.com/c/lofigirl", "timeout": 5},
    },
    {
        "name": "Get chat messages from livestream, using custom url (2).",
        "params": {"url": "https://www.youtube.com/lofigirl", "timeout": 5},
    },
    {
        "name": "Get chat messages from livestream, using user id.",
        "params": {
            "url": "https://www.youtube.com/user/YellowBrickCinema",
            "timeout": 5,
        },
    },
    {
        "name": "Get chat messages from live chat replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
            "max_messages": 10,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: 0 < len(messages) <= 10,
        },
    },
    {
        "name": "Get top chat messages from live chat replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
            "start_time": 0,
            "end_time": 20,
            "chat_type": "top",
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Get superchat and ticker messages from live chat replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=UlemRwXYWHg",
            "end_time": 20,
            "message_groups": ["superchat", "tickers"],
        },
        "expected_result": {
            "message_types": [
                "paid_message",
                "ticker_paid_message_item",
                "membership_item",
                "ticker_sponsor_item",
                "paid_sticker",
                "ticker_paid_sticker_item",
            ],
            "action_types": ["add_chat_item", "add_live_chat_ticker_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Get all messages from live chat replay",
        "params": {
            "url": "https://www.youtube.com/watch?v=97w16cYskVI",
            "end_time": 50,
            "message_types": ["all"],
        },
        "expected_result": {
            "message_types": [
                "viewer_engagement_message",
                "paid_message",
                "ticker_paid_message_item",
                "text_message",
                "paid_sticker",
                "ticker_paid_sticker_item",
            ],
            "action_types": ["add_chat_item", "add_live_chat_ticker_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Get messages from a premiere",  # Premiere
        "params": {
            "url": "https://www.youtube.com/watch?v=zVCs9Cug_qM",
            "start_time": 0,
            "end_time": 20,
        },
        "expected_result": {
            "message_types": ["text_message"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Chat replay with Super Chat messages",
        "params": {
            "url": "https://www.youtube.com/watch?v=UlemRwXYWHg",
            "start_time": 0,
            "end_time": 20,
            "message_groups": ["superchat"],
        },
        "expected_result": {
            "message_types": ["paid_message", "membership_item"],
            "action_types": ["add_chat_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Chat replay with membership gifts",
        "params": {
            "url": "https://www.youtube.com/watch?v=cb0h-KbpDo8",
            "start_time": "5:22:17",
            "end_time": "5:22:28",
            "message_groups": ["all"],
        },
        "expected_result": {
            "message_types": [
                "text_message",
                "sponsorships_gift_purchase_announcement",
                "ticker_sponsor_item",
            ],
            "action_types": ["add_chat_item", "add_live_chat_ticker_item"],
            "messages_condition": lambda messages: len(messages) > 0,
        },
    },
    {
        "name": "Get chat messages from an unplayable stream.",
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
    {
        "name": "Chat replay with a message that has no author name",
        "params": {
            "url": "https://www.youtube.com/watch?v=-JU0rbfPECY",
            "timeout": 5,
            "start_time": "1:53:29",
            "end_time": "1:53:30",
        },
    },
    # TESTING FOR ERRORS
    {
        "name": "Video does not exist",
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
        "params": {
            "url": "https://www.youtube.com/watch?v=cjk2UKkzY0g",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "This video is not available.",  # YouTube Premium
        "params": {
            "url": "https://www.youtube.com/watch?v=i1Ko8UG-Tdo",
        },
        "expected_result": {
            "error": VideoUnplayable,
        },
    },
    {
        "name": "This video is not available.",  # Rental video preview
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
        "params": {
            "url": "https://www.youtube.com/watch?v=6SJNVb0GnPI",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "Former members-only content with replay chat",
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
        "params": {
            "url": "https://www.youtube.com/watch?v=XWq5kBlakcQ",
        },
        "expected_result": {
            "error": [ChatDisabled, LoginRequired],
        },
    },
    {
        "name": "Live chat replay has been turned off for this video",
        "params": {
            "url": "https://www.youtube.com/watch?v=7lGZvbasx6A",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        "name": "Video is private",
        "params": {
            "url": "https://www.youtube.com/watch?v=ijFMXqa-N0c",
        },
        "expected_result": {
            "error": LoginRequired,
        },
    },
    {
        "name": ("The uploader has not made this video available in your country."),
        "params": {
            "url": "https://www.youtube.com/watch?v=sJL6WA-aGkQ",
        },
        "expected_result": {
            "error": VideoUnplayable,
        },
    },
    {
        "name": "This live stream recording is not available.",
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
        "params": {
            "url": "https://www.youtube.com/watch?v=WaOKSUlf4TM",
        },
        "expected_result": {
            "error": [LoginRequired, VideoUnavailable],
        },
    },
    # Potential parsing errors
    {
        "name": "Parsing error with '};' inside yt initial data (1)",
        "params": {
            "url": "https://www.youtube.com/watch?v=CHqg6qOn4no",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        "name": "Parsing error with '};' inside yt initial data (2)",
        "params": {
            "url": "https://www.youtube.com/watch?v=gVfgbahppCY",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        "name": 'Title with JS-like syntax "};"',
        "params": {
            "url": "https://www.youtube.com/watch?v=lsguqyKfVQg",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    # Clips
    {
        "name": "Chat replay of clip (past broadcast)",
        "params": {
            "url": "https://www.youtube.com/clip/Ugy_1IfsnZUWZSXL6C94AaABCQ",
            "max_messages": 3,
        },
        "expected_result": {
            "messages_condition": lambda messages: 0 < len(messages) <= 3,
        },
    },
    {
        "name": "Chat replay of clip (premiere)",
        "params": {
            "url": "https://www.youtube.com/clip/UgzNZCNnPzq-M3_Utjl4AaABCQ",
            "max_messages": 3,
        },
        "expected_result": {
            "messages_condition": lambda messages: 0 < len(messages) <= 3,
        },
    },
    {
        "name": "Clip does not have a chat replay.",
        "params": {
            "url": "https://www.youtube.com/clip/UgwVu73xQ5FUiGnteZJ4AaABCQ",
        },
        "expected_result": {
            "error": [NoChatReplay, LoginRequired],
        },
    },
    {
        # Age restricted -- YouTube may return LoginRequired or VideoUnavailable
        # depending on whether the age-gate check fires first.
        "name": (
            "Sign in to confirm your age. This clip may be inappropriate "
            "for some users."
        ),
        "params": {
            "url": "https://www.youtube.com/watch?v=_8W6Aoql-yk",
        },
        "expected_result": {
            "error": [LoginRequired, VideoUnavailable],
        },
    },
    {
        "name": (
            "Clip not available. The clip can be unavailable if it was "
            "deleted, or if the video it's based on was removed or edited."
        ),
        "params": {
            "url": "https://youtube.com/clip/UgxJiPo-4EeSYDfrYp94AaABCQ",
        },
        "expected_result": {
            "error": VideoUnavailable,
        },
    },
    {
        "name": "Clip does not exist",
        "params": {
            "url": "https://youtube.com/clip/x",
        },
        "expected_result": {
            "error": VideoNotFound,
        },
    },
]

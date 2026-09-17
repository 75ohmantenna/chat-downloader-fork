# SPDX-License-Identifier: MIT

"""IRC room state and moderation behavior through the assembled resolver."""

from __future__ import annotations

import pytest

from chat_downloader.sites.twitch.parsing.message_irc_resolve import (
    _parse_irc_int_flag,
    _resolve_irc_action_and_message_type,
)


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [(42, 0, 42), ("7", 0, 7), ("0", 99, 0), (None, 5, 5), (3.14, 5, 5), ([], 5, 5)],
)
def test_irc_int_flag(value: object, default: int, expected: int) -> None:
    assert _parse_irc_int_flag(value, default) == expected


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("follower_only", "-1", {"follower_only": False}),
        ("follower_only", "0", {"follower_only": True}),
        ("follower_only", 0, {"follower_only": True}),
        (
            "follower_only",
            "10",
            {"follower_only": True, "minutes_to_follow_before_chatting": 10},
        ),
        (
            "follower_only",
            30,
            {"follower_only": True, "minutes_to_follow_before_chatting": 30},
        ),
        ("slow_mode", "0", {"slow_mode": False}),
        ("slow_mode", 0, {"slow_mode": False}),
        ("slow_mode", "30", {"slow_mode": True, "seconds_to_wait": 30}),
        ("slow_mode", 15, {"slow_mode": True, "seconds_to_wait": 15}),
    ],
)
def test_room_state_normalization(field, value, expected) -> None:
    info = {field: value}
    _resolve_irc_action_and_message_type(info, "ROOMSTATE", None)
    assert info == {
        "action_type": "room_state",
        "message_type": "room_state",
        **expected,
    }


@pytest.mark.parametrize(
    ("action", "message", "duration", "expected"),
    [
        ("", None, None, {"message_type": ""}),
        ("CLEARCHAT", "targeted_user", 600, {"ban_type": "timeout"}),
        ("CLEARCHAT", "targeted_user", None, {"ban_type": "permanent"}),
        ("CLEARCHAT", None, None, {}),
        ("PRIVMSG", "someone", None, {}),
    ],
)
def test_action_resolution(action, message, duration, expected) -> None:
    info = {"message": message} if message else {}
    if duration is not None:
        info["ban_duration"] = duration
    _resolve_irc_action_and_message_type(info, action, message)
    if "ban_type" in expected:
        assert info["message_type"] == "ban_user"
        assert info["ban_type"] == expected["ban_type"]
        assert info["banned_user"] == message
        assert "message" not in info
    else:
        assert info["message_type"] != "ban_user"
        assert "ban_type" not in info
        if not action:
            assert info == expected

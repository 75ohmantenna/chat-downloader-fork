# SPDX-License-Identifier: MIT

"""Isolated unit tests for message_irc_resolve pure helper functions."""

import pytest

from chat_downloader.sites.twitch.parsing.message_irc_resolve import (
    _normalize_follower_only,
    _normalize_slow_mode,
    _parse_irc_int_flag,
    _resolve_clearchat_ban,
)


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (42, 0, 42),
        ("7", 0, 7),
        ("0", 99, 0),
        (None, 5, 5),
        (3.14, 5, 5),
        ([], 5, 5),
    ],
)
def test_parse_irc_int_flag_converts_int_and_str_falls_back_for_others(
    value: object, default: int, expected: int
) -> None:
    assert _parse_irc_int_flag(value, default) == expected


@pytest.mark.parametrize(
    ("follower_only_value", "expected_flag", "expect_duration_key"),
    [
        ("-1", False, False),
        ("0", True, False),
        ("10", True, True),
        (0, True, False),
        (30, True, True),
    ],
)
def test_normalize_follower_only_sets_bool_and_optional_duration(
    follower_only_value: object,
    expected_flag: bool,
    expect_duration_key: bool,
) -> None:
    info: dict[str, object] = {"follower_only": follower_only_value}
    _normalize_follower_only(info)
    assert info["follower_only"] is expected_flag
    if expect_duration_key:
        assert "minutes_to_follow_before_chatting" in info
    else:
        assert "minutes_to_follow_before_chatting" not in info


def test_normalize_follower_only_skips_when_absent() -> None:
    info: dict[str, object] = {}
    _normalize_follower_only(info)
    assert "follower_only" not in info


@pytest.mark.parametrize(
    ("slow_mode_value", "expected_flag", "expect_duration"),
    [
        ("0", False, False),
        (0, False, False),
        ("30", True, True),
        (15, True, True),
    ],
)
def test_normalize_slow_mode_sets_bool_and_optional_duration(
    slow_mode_value: object,
    expected_flag: bool,
    expect_duration: bool,
) -> None:
    info: dict[str, object] = {"slow_mode": slow_mode_value}
    _normalize_slow_mode(info)
    assert info["slow_mode"] is expected_flag
    if expect_duration:
        assert "seconds_to_wait" in info
    else:
        assert "seconds_to_wait" not in info


def test_normalize_slow_mode_skips_when_absent() -> None:
    info: dict[str, object] = {}
    _normalize_slow_mode(info)
    assert "slow_mode" not in info


def test_resolve_clearchat_ban_timeout_when_ban_duration_present() -> None:
    info: dict[str, object] = {
        "ban_duration": 600,
        "message": "targeted_user",
    }
    _resolve_clearchat_ban(info, "CLEARCHAT", "targeted_user")
    assert info["message_type"] == "ban_user"
    assert info["ban_type"] == "timeout"
    assert info["banned_user"] == "targeted_user"
    assert "message" not in info


def test_resolve_clearchat_ban_permanent_when_no_ban_duration() -> None:
    info: dict[str, object] = {"message": "targeted_user"}
    _resolve_clearchat_ban(info, "CLEARCHAT", "targeted_user")
    assert info["message_type"] == "ban_user"
    assert info["ban_type"] == "permanent"
    assert info["banned_user"] == "targeted_user"


def test_resolve_clearchat_ban_no_op_when_no_target_user() -> None:
    info: dict[str, object] = {}
    _resolve_clearchat_ban(info, "CLEARCHAT", None)
    assert "message_type" not in info
    assert "ban_type" not in info


def test_resolve_clearchat_ban_no_op_for_non_clearchat_action() -> None:
    info: dict[str, object] = {"message": "someone"}
    _resolve_clearchat_ban(info, "PRIVMSG", "someone")
    assert "ban_type" not in info

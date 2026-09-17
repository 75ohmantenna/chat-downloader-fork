# SPDX-License-Identifier: MIT

"""Contract ratchets for the observable CLI parser surface."""

from __future__ import annotations

import argparse

import pytest

from chat_downloader.cli import _build_arg_parser
from chat_downloader.models import SiteDefault

EXPECTED_OPTION_STRINGS = frozenset(
    [
        "--auto_profile_fallback",
        "--buffer_size",
        "--chat_type",
        "--connect_timeout",
        "--cookies",
        "--end_time",
        "--exit_on_debug",
        "--format",
        "--format_file",
        "--header",
        "--help",
        "--ignore",
        "--inactivity_timeout",
        "--interruptible_retry",
        "--logging",
        "--max_attempts",
        "--max_messages",
        "--message_groups",
        "--message_receive_timeout",
        "--message_types",
        "--output",
        "--overwrite",
        "--pause_on_debug",
        "--proxy",
        "--quiet",
        "--read_timeout",
        "--request_profile",
        "--require_complete",
        "--resume",
        "--retry_timeout",
        "--run_manifest",
        "--sort_keys",
        "--start_time",
        "--testing",
        "--timeout",
        "--twitch_client_id",
        "--user-agent",
        "--verbose",
        "--verify_output",
        "--version",
        "--youtube_replay_poll_interval",
        "-c",
        "-e",
        "-h",
        "-o",
        "-p",
        "-q",
        "-s",
        "-v",
    ]
)

EXPECTED_DEFAULTS = {
    **dict.fromkeys(
        [
            "cookies",
            "end_time",
            "format_file",
            "headers_list",
            "ignore",
            "inactivity_timeout",
            "max_messages",
            "message_types",
            "output",
            "proxy",
            "resume",
            "run_manifest",
            "request_profile",
            "retry_timeout",
            "start_time",
            "timeout",
            "twitch_client_id",
            "user_agent",
            "youtube_replay_poll_interval",
        ],
        None,
    ),
    **dict.fromkeys(
        [
            "exit_on_debug",
            "pause_on_debug",
            "quiet",
            "verify_output",
            "require_complete",
            "testing",
            "verbose",
        ],
        False,
    ),
    "auto_profile_fallback": True,
    "buffer_size": 4096,
    "chat_type": "live",
    "connect_timeout": 10.0,
    "format": ("SiteDefault", "format"),
    "help": argparse.SUPPRESS,
    "interruptible_retry": True,
    "logging": "info",
    "max_attempts": 15,
    "message_groups": ("SiteDefault", "message_groups"),
    "message_receive_timeout": 1.0,
    "overwrite": True,
    "read_timeout": 30.0,
    "sort_keys": True,
    "url": "",
    "version": argparse.SUPPRESS,
}

EXPECTED_GROUP_MEMBERS = {
    "Mandatory Arguments": ("url",),
    "General Arguments": ("help", "version"),
    "Timing Arguments": ("start_time", "end_time"),
    "Message Type Arguments": ("message_types", "message_groups"),
    "Retry Arguments": ("max_attempts", "retry_timeout", "interruptible_retry"),
    "Termination Arguments": ("max_messages", "inactivity_timeout", "timeout"),
    "Format Arguments": ("format", "format_file"),
    "[Site Specific] YouTube Arguments": (
        "chat_type",
        "ignore",
        "youtube_replay_poll_interval",
    ),
    "Live Transport Arguments": ("message_receive_timeout",),
    "[Site Specific] Twitch Arguments": ("buffer_size",),
    "Output Arguments": (
        "output",
        "overwrite",
        "sort_keys",
        "resume",
        "verify_output",
        "require_complete",
        "run_manifest",
    ),
    "Debugging/Testing Arguments": (
        "pause_on_debug",
        "exit_on_debug",
        "logging",
        "testing",
        "verbose",
        "quiet",
    ),
    "Initialization Arguments": (
        "cookies",
        "proxy",
        "connect_timeout",
        "read_timeout",
        "request_profile",
        "auto_profile_fallback",
        "twitch_client_id",
        "user_agent",
        "headers_list",
    ),
}


@pytest.fixture
def parser():
    return _build_arg_parser()


def test_cli_surface_is_stable(parser) -> None:
    assert {
        option for action in parser._actions for option in action.option_strings
    } == EXPECTED_OPTION_STRINGS
    assert {
        action.dest: ("SiteDefault", action.default.name)
        if isinstance(action.default, SiteDefault)
        else action.default
        for action in parser._actions
    } == EXPECTED_DEFAULTS
    assert {
        group.title: tuple(action.dest for action in group._group_actions)
        for group in parser._action_groups
    } == EXPECTED_GROUP_MEMBERS


@pytest.mark.parametrize(
    ("option", "value", "expected"),
    [
        (
            "--message_groups",
            "messages,subscriptions,moderation",
            ["messages", "subscriptions", "moderation"],
        ),
        (
            "--message_types",
            "messages,subscriptions,moderation",
            ["messages", "subscriptions", "moderation"],
        ),
        ("--message_groups", "all", ["all"]),
    ],
)
def test_cli_message_filters(parser, option, value, expected) -> None:
    args = parser.parse_args(["https://kick.com/example", option, value])
    assert getattr(args, option.removeprefix("--")) == expected


def test_cli_message_filters_reject_unquoted_multiple_arguments(parser) -> None:
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "https://kick.com/example",
                "--message_groups",
                "messages",
                "subscriptions",
                "moderation",
            ]
        )

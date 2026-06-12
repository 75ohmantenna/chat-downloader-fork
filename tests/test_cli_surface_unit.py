# SPDX-License-Identifier: MIT

"""Contract tests for the observable CLI parser surface."""

from __future__ import annotations

import argparse
from typing import Any

from chat_downloader.cli import _build_arg_parser
from chat_downloader.models import SiteDefault

EXPECTED_OPTION_STRINGS: frozenset[str] = frozenset(
    {
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
        "--retry_timeout",
        "--sort_keys",
        "--start_time",
        "--testing",
        "--timeout",
        "--twitch_client_id",
        "--user-agent",
        "--verbose",
        "--version",
        "-c",
        "-e",
        "-h",
        "-o",
        "-p",
        "-q",
        "-s",
        "-v",
    }
)

EXPECTED_DEFAULTS: dict[str, Any] = {
    "auto_profile_fallback": True,
    "buffer_size": 4096,
    "chat_type": "live",
    "connect_timeout": 10.0,
    "cookies": None,
    "end_time": None,
    "exit_on_debug": False,
    "format": ("SiteDefault", "format"),
    "format_file": None,
    "headers_list": None,
    "help": argparse.SUPPRESS,
    "ignore": None,
    "inactivity_timeout": None,
    "interruptible_retry": True,
    "logging": "info",
    "max_attempts": 15,
    "max_messages": None,
    "message_groups": ("SiteDefault", "message_groups"),
    "message_receive_timeout": 0.1,
    "message_types": None,
    "output": None,
    "overwrite": True,
    "pause_on_debug": False,
    "proxy": None,
    "quiet": False,
    "read_timeout": 30.0,
    "request_profile": None,
    "retry_timeout": None,
    "sort_keys": True,
    "start_time": None,
    "testing": False,
    "timeout": None,
    "twitch_client_id": None,
    "url": "",
    "user_agent": None,
    "verbose": False,
    "version": argparse.SUPPRESS,
}

EXPECTED_GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    "Mandatory Arguments": ("url",),
    "General Arguments": ("help", "version"),
    "Timing Arguments": ("start_time", "end_time"),
    "Message Type Arguments": ("message_types", "message_groups"),
    "Retry Arguments": (
        "max_attempts",
        "retry_timeout",
        "interruptible_retry",
    ),
    "Termination Arguments": (
        "max_messages",
        "inactivity_timeout",
        "timeout",
    ),
    "Format Arguments": ("format", "format_file"),
    "[Site Specific] YouTube Arguments": ("chat_type", "ignore"),
    "[Site Specific] Twitch Arguments": (
        "message_receive_timeout",
        "buffer_size",
    ),
    "Output Arguments": ("output", "overwrite", "sort_keys"),
    "Debugging/Testing Arguments": (
        "pause_on_debug",
        "exit_on_debug",
        "logging",
        "testing",
        "verbose",
        "quiet",
    ),
    "Initialisation Arguments": (
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


def _normalize_default(value: object) -> object:
    if isinstance(value, SiteDefault):
        return ("SiteDefault", value.name)
    return value


def test_cli_option_strings_are_stable() -> None:
    parser = _build_arg_parser()
    option_strings = {
        option for action in parser._actions for option in action.option_strings
    }

    assert option_strings == EXPECTED_OPTION_STRINGS


def test_cli_option_defaults_are_stable() -> None:
    parser = _build_arg_parser()
    defaults = {
        action.dest: _normalize_default(action.default) for action in parser._actions
    }

    assert defaults == EXPECTED_DEFAULTS


def test_cli_argument_group_membership_is_stable() -> None:
    parser = _build_arg_parser()
    group_members = {
        group.title: tuple(action.dest for action in group._group_actions)
        for group in parser._action_groups
    }

    assert group_members == EXPECTED_GROUP_MEMBERS

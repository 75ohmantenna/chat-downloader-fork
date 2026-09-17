# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import dataclasses
from contextlib import nullcontext
from unittest.mock import patch

import pytest

import chat_downloader.cli_args as cli_args_module
from chat_downloader.cli import main
from chat_downloader.cli_args import (
    _build_request_headers,
    parse_header,
    splitter,
    str2bool,
)
from chat_downloader.models import ChatRequest, DownloaderConfig, RunConfig
from chat_downloader.runtime.runner import RunResult

URL = "https://example.com/watch?v=fake"


def _run_and_capture(*extra_args) -> dict:
    with patch("chat_downloader.cli.run", return_value=RunResult(success=True)) as run:
        main([URL, *extra_args])
    return run.call_args.kwargs


@pytest.mark.parametrize(
    "result",
    [
        RunResult(success=False, error_message="boom"),
        RunResult(success=True, interrupted=True),
        RunResult(success=True),
    ],
    ids=["failed", "interrupted", "success"],
)
def test_cli_exit_status(result) -> None:
    failed = not result.success or result.interrupted
    with (
        patch("chat_downloader.cli.run", return_value=result),
        pytest.raises(SystemExit) if failed else nullcontext() as exc_info,
    ):
        main([URL])
    if failed:
        assert exc_info.value.code == 1


def test_cli_invalid_request_exits_without_traceback(caplog) -> None:
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as exc_info:
        main([URL, "--max_attempts", "0"])
    assert exc_info.value.code == 1
    assert "max_attempts" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("value", ["a,b,c", "a b c", "a;b;c", "a, b; c", "only"])
def test_splitter(value) -> None:
    assert splitter(value) == (["only"] if value == "only" else ["a", "b", "c"])


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([True, "true", "yes", "t", "y", "1", "enable", "True", "YES"], True),
        ([False, "false", "no", "f", "n", "0", "disable", "False", "NO"], False),
    ],
)
def test_str2bool(values, expected) -> None:
    for value in values:
        assert str2bool(value) is expected


@pytest.mark.parametrize(
    ("parse", "value"),
    [(str2bool, "maybe")]
    + [
        (parse_header, value)
        for value in (
            "BrokenHeader",
            "X-Test: hello\r\nInjected: nope",
            "Bad Header: value",
            ":somevalue",
        )
    ],
)
def test_cli_value_parsers_reject_invalid_input(parse, value) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse(value)


@pytest.mark.parametrize(
    ("flags", "level", "disabled"),
    [
        (["--testing"], "debug", False),
        (["--verbose"], "debug", False),
        (["--quiet"], "info", False),
        (["--logging", "none"], None, True),
        (["--quiet", "--logging", "debug"], "debug", False),
    ],
)
def test_logging_controls(flags, level, disabled) -> None:
    with (
        patch("chat_downloader.cli.set_log_level") as set_level,
        patch("chat_downloader.cli.disable_logger") as disable,
    ):
        kwargs = _run_and_capture(*flags)
    assert disable.called is disabled
    if level is not None:
        set_level.assert_called_once_with(level)
    assert "logging" not in kwargs
    assert kwargs["quiet"] is ("--quiet" in flags)
    assert kwargs["pause_on_debug"] is ("--testing" in flags)


@pytest.mark.parametrize("flag", ["pause_on_debug", "exit_on_debug"])
def test_debug_control_flag(flag) -> None:
    assert _run_and_capture(f"--{flag}")[flag] is True


@pytest.mark.parametrize(
    ("arguments", "headers"),
    [
        (["--user-agent", "MyBot/1.0"], {"User-Agent": "MyBot/1.0"}),
        (["--header", "Accept-Language: en"], {"Accept-Language": "en"}),
        (["--header", "X-Foo:   bar   "], {"X-Foo": "bar"}),
        (["--header", "X-Key:value"], {"X-Key": "value"}),
        (
            ["--header", "X-A: one", "--header", "X-B: two"],
            {"X-A": "one", "X-B": "two"},
        ),
        (
            ["--user-agent", "TestAgent/2.0", "--header", "Accept: application/json"],
            {"User-Agent": "TestAgent/2.0", "Accept": "application/json"},
        ),
        (["--user-agent", "UA", "--header", "user-agent: CLI"], {"User-Agent": "CLI"}),
        (
            ["--header", "Authorization: Bearer tok:en"],
            {"Authorization": "Bearer tok:en"},
        ),
        (
            ["--request_profile", "youtube_ios", "--user-agent", "Override/9.9"],
            {"User-Agent": "Override/9.9"},
        ),
        (
            [
                "--request_profile",
                "twitch_web",
                "--header",
                "Accept-Language: de-DE,de;q=0.8",
            ],
            {"Accept-Language": "de-DE,de;q=0.8"},
        ),
    ],
)
def test_explicit_headers_are_normalized_and_merged(arguments, headers) -> None:
    assert _run_and_capture(*arguments)["headers"] == headers


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], {"auto_profile_fallback": True}),
        (
            ["--request_profile", "youtube_android"],
            {"request_profile": "youtube_android"},
        ),
        (
            ["--twitch_client_id", "custom-client"],
            {"twitch_client_id": "custom-client"},
        ),
        (["--auto_profile_fallback", "false"], {"auto_profile_fallback": False}),
        (
            ["-c", "/tmp/cookies.txt", "-p", "socks5://127.0.0.1:1080"],
            {"cookies": "/tmp/cookies.txt", "proxy": "socks5://127.0.0.1:1080"},
        ),
        (
            [
                "--cookies",
                "/tmp/cookies.txt",
                "--proxy",
                "socks5://127.0.0.1:1080",
                "--connect_timeout",
                "12.5",
                "--read_timeout",
                "33.5",
                "--request_profile",
                "youtube_web",
                "--auto_profile_fallback",
                "false",
            ],
            {
                "cookies": "/tmp/cookies.txt",
                "proxy": "socks5://127.0.0.1:1080",
                "connect_timeout": 12.5,
                "read_timeout": 33.5,
                "request_profile": "youtube_web",
                "auto_profile_fallback": False,
            },
        ),
    ],
)
def test_init_session_arguments(arguments, expected) -> None:
    actual = _run_and_capture(*arguments)
    assert {key: actual[key] for key in expected} == expected
    assert "headers" not in actual


@pytest.mark.parametrize("missing", [False, True])
def test_cli_registration_uses_dataclass_metadata(monkeypatch, missing) -> None:
    original = cli_args_module._build_field_info

    def build_field_info(dc_class):
        info = original(dc_class)
        if missing and dc_class is ChatRequest:
            info.pop("url")
        elif not missing and dc_class is DownloaderConfig:
            info["connect_timeout"]["flags"] = ["-T"]
        return info

    monkeypatch.setattr(cli_args_module, "_build_field_info", build_field_info)
    if missing:
        with pytest.raises(RuntimeError, match="no matching dataclass CLI metadata"):
            main([URL])
    else:
        assert _run_and_capture("-T", "12.5")["connect_timeout"] == 12.5


def test_invalid_header_flag_raises_parse_error() -> None:
    with pytest.raises(SystemExit):
        main([URL, "--header", "BrokenHeader"])


def test_parse_header_returns_key_value_pair() -> None:
    assert parse_header("X-Test: value") == ("X-Test", "value")


_CLI_CHAT_PARAMS = frozenset(
    [
        "url",
        "start_time",
        "end_time",
        "message_types",
        "message_groups",
        "max_attempts",
        "retry_timeout",
        "interruptible_retry",
        "max_messages",
        "inactivity_timeout",
        "timeout",
        "format",
        "format_file",
        "chat_type",
        "ignore",
        "youtube_replay_poll_interval",
        "message_receive_timeout",
        "buffer_size",
        "output",
        "overwrite",
        "sort_keys",
    ]
)


def test_cli_chat_fields_and_mapping_keys_match() -> None:
    assert {f.name for f in dataclasses.fields(ChatRequest)} == _CLI_CHAT_PARAMS
    assert set(ChatRequest().as_dict()) == _CLI_CHAT_PARAMS


def test_run_config_cli_flags_match_metadata() -> None:
    assert {f.name for f in dataclasses.fields(RunConfig) if f.metadata.get("cli")} == {
        "quiet",
        "pause_on_debug",
        "exit_on_debug",
        "resume",
        "verify_output",
        "require_complete",
        "run_manifest",
    }


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"request_profile": None}, {}),
        (
            {"request_profile": None, "user_agent": "UA", "headers_list": {}},
            {"User-Agent": "UA"},
        ),
        (
            {
                "request_profile": None,
                "user_agent": "from-ua",
                "headers_list": {"User-Agent": "from-header"},
            },
            {"User-Agent": "from-header"},
        ),
        (
            {"request_profile": "youtube_web", "user_agent": "custom-ua"},
            {"User-Agent": "custom-ua"},
        ),
    ],
)
def test_build_request_headers_removes_cli_keys_and_respects_precedence(
    arguments, expected
):
    assert _build_request_headers(arguments) == expected
    assert "user_agent" not in arguments
    assert "headers_list" not in arguments

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import dataclasses
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

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_and_capture(*extra_args) -> dict:
    """Run main() with a dummy URL and return the keywords passed to run()."""
    captured: dict = {}

    def fake_run(**kwargs) -> RunResult:
        captured.update(kwargs)
        return RunResult(success=True)

    with patch("chat_downloader.cli.run", side_effect=fake_run):
        main(["https://example.com/watch?v=fake", *extra_args])
    return captured


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_cli_calls_run() -> None:
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    with patch("chat_downloader.cli.run") as mock_run:
        mock_run.return_value = RunResult(success=True)
        main([url, "--timeout", "10"])
    mock_run.assert_called_once()


@pytest.mark.parametrize(
    "result",
    [
        RunResult(success=False, error_message="boom"),
        RunResult(success=True, interrupted=True),
    ],
    ids=["failed", "interrupted"],
)
def test_cli_exits_nonzero_on_failure(result: RunResult) -> None:
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    with (
        patch("chat_downloader.cli.run", return_value=result),
        pytest.raises(SystemExit) as exc_info,
    ):
        main([url])
    assert exc_info.value.code == 1


def test_cli_no_exit_on_success() -> None:
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    with patch(
        "chat_downloader.cli.run",
        return_value=RunResult(success=True),
    ):
        main([url])  # must not raise SystemExit


def test_cli_invalid_request_exits_without_traceback(caplog) -> None:
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as exc_info:
        main([url, "--max_attempts", "0"])

    assert exc_info.value.code == 1
    assert "max_attempts" in caplog.text
    assert "Traceback" not in caplog.text


# ---------------------------------------------------------------------------
# splitter()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a,b,c", ["a", "b", "c"]),
        ("a b c", ["a", "b", "c"]),
        ("a;b;c", ["a", "b", "c"]),
        ("a, b; c", ["a", "b", "c"]),
        ("only", ["only"]),
    ],
)
def test_splitter(value: str, expected: list) -> None:
    assert splitter(value) == expected


# ---------------------------------------------------------------------------
# str2bool()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_str2bool_already_bool(value) -> None:
    assert str2bool(value) is value


@pytest.mark.parametrize("val", ["true", "yes", "t", "y", "1", "enable", "True", "YES"])
def test_str2bool_true_strings(val: str) -> None:
    assert str2bool(val)


@pytest.mark.parametrize(
    "val", ["false", "no", "f", "n", "0", "disable", "False", "NO"]
)
def test_str2bool_false_strings(val: str) -> None:
    assert not str2bool(val)


def test_str2bool_invalid_raises() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        str2bool("maybe")


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--testing", "--verbose"])
def test_debug_shortcuts_set_logging(flag) -> None:
    with (
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        call_kwargs = _run_and_capture(flag)
    mock_set_level.assert_called_once_with("debug")
    assert call_kwargs["pause_on_debug"] is (flag == "--testing")
    assert "logging" not in call_kwargs


@pytest.mark.parametrize("flag", ["pause_on_debug", "exit_on_debug"])
def test_debug_control_flag(flag) -> None:
    assert _run_and_capture(f"--{flag}")[flag] is True


def test_quiet_flag_disables_logger() -> None:
    with (
        patch("chat_downloader.cli.run", return_value=RunResult(success=True)),
        patch("chat_downloader.cli.disable_logger") as mock_disable,
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        main(["https://example.com/watch?v=fake", "--quiet"])
    mock_disable.assert_not_called()
    mock_set_level.assert_called_once_with("info")


def test_logging_none_disables_logger() -> None:
    with (
        patch("chat_downloader.cli.run", return_value=RunResult(success=True)),
        patch("chat_downloader.cli.disable_logger") as mock_disable,
    ):
        main(["https://example.com/watch?v=fake", "--logging", "none"])
    mock_disable.assert_called_once()


def test_quiet_can_be_combined_with_logging() -> None:
    with (
        patch(
            "chat_downloader.cli.run",
            return_value=RunResult(success=True),
        ) as mock_run,
        patch("chat_downloader.cli.disable_logger") as mock_disable,
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        main(
            [
                "https://example.com/watch?v=fake",
                "--quiet",
                "--logging",
                "debug",
            ]
        )
    mock_disable.assert_not_called()
    mock_set_level.assert_called_once_with("debug")
    assert mock_run.call_args.kwargs["quiet"]
    assert "logging" not in mock_run.call_args.kwargs


def test_default_run_debug_flags_are_false() -> None:
    with patch(
        "chat_downloader.cli.run", return_value=RunResult(success=True)
    ) as mock_run:
        main(["https://example.com/watch?v=fake"])
    assert mock_run.call_args.kwargs["quiet"] is False
    assert mock_run.call_args.kwargs["pause_on_debug"] is False
    assert mock_run.call_args.kwargs["exit_on_debug"] is False


# ---------------------------------------------------------------------------
# Header / init session flags
# ---------------------------------------------------------------------------


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
        (
            ["--user-agent", "UA", "--header", "user-agent: CLI"],
            {"User-Agent": "CLI"},
        ),
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


def test_request_profile_sets_preset_headers() -> None:
    d = _run_and_capture("--request_profile", "youtube_android")
    assert d.get("request_profile") == "youtube_android"
    assert "headers" not in d


def test_twitch_client_id_is_init_parameter() -> None:
    d = _run_and_capture("--twitch_client_id", "custom-client")
    assert d.get("twitch_client_id") == "custom-client"


def test_no_header_flags_omits_headers_key() -> None:
    assert "headers" not in _run_and_capture()


@pytest.mark.parametrize(("arguments", "expected"), [([], True), (["false"], False)])
def test_auto_profile_fallback(arguments, expected) -> None:
    flags = ["--auto_profile_fallback", *arguments] if arguments else []
    assert _run_and_capture(*flags)["auto_profile_fallback"] is expected


def test_init_session_args_are_forwarded() -> None:
    d = _run_and_capture(
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
    )
    assert d.get("cookies") == "/tmp/cookies.txt"
    assert d.get("proxy") == "socks5://127.0.0.1:1080"
    assert d.get("connect_timeout") == 12.5
    assert d.get("read_timeout") == 33.5
    assert d.get("request_profile") == "youtube_web"
    assert d.get("auto_profile_fallback") is False


def test_init_session_short_flags_are_forwarded() -> None:
    d = _run_and_capture("-c", "/tmp/cookies.txt", "-p", "socks5://127.0.0.1:1080")
    assert d.get("cookies") == "/tmp/cookies.txt"
    assert d.get("proxy") == "socks5://127.0.0.1:1080"


def test_metadata_flags_are_added_when_not_explicitly_declared() -> None:
    original = cli_args_module._build_field_info

    def fake_build_field_info(dc_class):
        info = original(dc_class)
        if dc_class is DownloaderConfig:
            info["connect_timeout"]["flags"] = ["-T"]
        return info

    with (
        patch(
            "chat_downloader.cli_args._build_field_info",
            side_effect=fake_build_field_info,
        ),
        patch(
            "chat_downloader.cli.run",
            return_value=RunResult(success=True),
        ) as mock_run,
    ):
        main(["https://example.com/watch?v=fake", "-T", "12.5"])

    assert mock_run.call_args.kwargs["connect_timeout"] == 12.5


def test_invalid_header_flag_raises_parse_error() -> None:
    with pytest.raises(SystemExit):
        main(["https://example.com/watch?v=fake", "--header", "BrokenHeader"])


# ---------------------------------------------------------------------------
# parse_header()
# ---------------------------------------------------------------------------


def test_parse_header_returns_key_value_pair() -> None:
    assert parse_header("X-Test: value") == ("X-Test", "value")


@pytest.mark.parametrize(
    "value",
    [
        "BrokenHeader",
        "X-Test: hello\r\nInjected: nope",
        "Bad Header: value",
        ":somevalue",
    ],
)
def test_parse_header_rejects_invalid_input(value) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_header(value)


# ---------------------------------------------------------------------------
# CLI ↔ ChatRequest parity
# ---------------------------------------------------------------------------

_CLI_CHAT_PARAMS = frozenset(
    {
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
    }
)


def test_cli_chat_fields_and_mapping_keys_match() -> None:
    assert {f.name for f in dataclasses.fields(ChatRequest)} == _CLI_CHAT_PARAMS
    assert set(ChatRequest().as_dict()) == _CLI_CHAT_PARAMS


def test_cli_registration_fails_fast_without_dataclass_metadata(
    monkeypatch,
) -> None:
    original_build_field_info = cli_args_module._build_field_info

    def fake_build_field_info(dc_class):
        info = original_build_field_info(dc_class)
        if dc_class is ChatRequest:
            info.pop("url")
        return info

    monkeypatch.setattr(
        "chat_downloader.cli_args._build_field_info",
        fake_build_field_info,
    )

    with pytest.raises(RuntimeError, match="no matching dataclass CLI metadata"):
        main(["https://example.com/watch?v=fake"])


def test_run_config_cli_flags_match_metadata() -> None:
    cli_fields = {
        f.name for f in dataclasses.fields(RunConfig) if f.metadata.get("cli")
    }
    assert cli_fields == {
        "quiet",
        "pause_on_debug",
        "exit_on_debug",
        "resume",
        "verify_output",
        "require_complete",
        "run_manifest",
    }


# ---------------------------------------------------------------------------
# _build_request_headers
# ---------------------------------------------------------------------------


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
) -> None:
    assert _build_request_headers(arguments) == expected
    assert "user_agent" not in arguments
    assert "headers_list" not in arguments

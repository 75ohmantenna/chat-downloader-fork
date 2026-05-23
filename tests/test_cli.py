# SPDX-License-Identifier: MIT

import argparse
import dataclasses
from unittest.mock import patch

import pytest

import chat_downloader.cli as cli_module
from chat_downloader.cli import (
    REQUEST_PROFILES,
    main,
    parse_header,
    splitter,
    str2bool,
)
from chat_downloader.models import ChatRequest, DownloaderConfig, RunConfig

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_and_capture(*extra_args) -> dict:
    """Run main() with a dummy URL; return the full kwargs dict passed to run()."""
    captured: dict = {}

    def fake_run(**kwargs) -> None:
        captured.update(kwargs)

    with patch("chat_downloader.cli.run", side_effect=fake_run):
        main(["https://example.com/watch?v=fake", *extra_args])
    return captured


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_cli_calls_run() -> None:
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    with patch("chat_downloader.cli.run") as mock_run:
        main([url, "--timeout", "10"])
    mock_run.assert_called_once()


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


def test_str2bool_already_bool_true() -> None:
    assert str2bool(True)


def test_str2bool_already_bool_false() -> None:
    assert not str2bool(False)


@pytest.mark.parametrize(
    "val", ["true", "yes", "t", "y", "1", "enable", "True", "YES"]
)
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


def test_testing_flag_sets_logging_and_pause() -> None:
    with (
        patch("chat_downloader.cli.run") as mock_run,
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        main(["https://example.com/watch?v=fake", "--testing"])
    call_kwargs = mock_run.call_args[1]
    mock_set_level.assert_called_once_with("debug")
    assert call_kwargs.get("pause_on_debug")
    assert "logging" not in call_kwargs


def test_verbose_flag_sets_logging_debug() -> None:
    with (
        patch("chat_downloader.cli.run") as mock_run,
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        main(["https://example.com/watch?v=fake", "--verbose"])
    call_kwargs = mock_run.call_args[1]
    mock_set_level.assert_called_once_with("debug")
    assert "logging" not in call_kwargs


def test_pause_on_debug_flag() -> None:
    with patch("chat_downloader.cli.run") as mock_run:
        main(["https://example.com/watch?v=fake", "--pause_on_debug"])
    assert mock_run.call_args.kwargs["pause_on_debug"]


def test_exit_on_debug_flag() -> None:
    with patch("chat_downloader.cli.run") as mock_run:
        main(["https://example.com/watch?v=fake", "--exit_on_debug"])
    assert mock_run.call_args.kwargs["exit_on_debug"]


def test_quiet_flag_disables_logger() -> None:
    with (
        patch("chat_downloader.cli.run"),
        patch("chat_downloader.cli.disable_logger") as mock_disable,
        patch("chat_downloader.cli.set_log_level") as mock_set_level,
    ):
        main(["https://example.com/watch?v=fake", "--quiet"])
    mock_disable.assert_not_called()
    mock_set_level.assert_called_once_with("info")


def test_logging_none_disables_logger() -> None:
    with (
        patch("chat_downloader.cli.run"),
        patch("chat_downloader.cli.disable_logger") as mock_disable,
    ):
        main(["https://example.com/watch?v=fake", "--logging", "none"])
    mock_disable.assert_called_once()


def test_quiet_can_be_combined_with_logging() -> None:
    with (
        patch("chat_downloader.cli.run") as mock_run,
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
    with patch("chat_downloader.cli.run") as mock_run:
        main(["https://example.com/watch?v=fake"])
    assert mock_run.call_args.kwargs["quiet"] is False
    assert mock_run.call_args.kwargs["pause_on_debug"] is False
    assert mock_run.call_args.kwargs["exit_on_debug"] is False


# ---------------------------------------------------------------------------
# Header / init session flags
# ---------------------------------------------------------------------------


def test_user_agent_sets_user_agent_header() -> None:
    d = _run_and_capture("--user-agent", "MyBot/1.0")
    assert d.get("headers", {}).get("User-Agent") == "MyBot/1.0"


def test_request_profile_sets_preset_headers() -> None:
    d = _run_and_capture("--request_profile", "youtube_android")
    assert d.get("request_profile") == "youtube_android"
    assert d.get("headers", {}) == REQUEST_PROFILES["youtube_android"]


def test_user_agent_overrides_profile_user_agent() -> None:
    d = _run_and_capture(
        "--request_profile", "youtube_ios", "--user-agent", "Override/9.9"
    )
    assert d.get("headers", {}).get("User-Agent") == "Override/9.9"
    assert d.get("headers", {}).get("Accept-Language") == "en-US,en;q=0.9"


def test_header_flag_overrides_profile_values() -> None:
    d = _run_and_capture(
        "--request_profile",
        "twitch_web",
        "--header",
        "Accept-Language: de-DE,de;q=0.8",
    )
    assert d.get("headers", {}).get("Accept-Language") == "de-DE,de;q=0.8"


def test_twitch_client_id_is_init_parameter() -> None:
    d = _run_and_capture("--twitch_client_id", "custom-client")
    assert d.get("twitch_client_id") == "custom-client"


def test_header_flag_parses_colon_separated() -> None:
    d = _run_and_capture("--header", "Accept-Language: en")
    assert d.get("headers", {}).get("Accept-Language") == "en"


def test_header_flag_strips_whitespace_from_value() -> None:
    d = _run_and_capture("--header", "X-Foo:   bar   ")
    assert d.get("headers", {}).get("X-Foo") == "bar"


def test_header_flag_no_space_after_colon() -> None:
    d = _run_and_capture("--header", "X-Key:value")
    assert d.get("headers", {}).get("X-Key") == "value"


def test_multiple_header_flags() -> None:
    d = _run_and_capture("--header", "X-A: one", "--header", "X-B: two")
    assert d.get("headers", {}).get("X-A") == "one"
    assert d.get("headers", {}).get("X-B") == "two"


def test_user_agent_and_header_combined() -> None:
    d = _run_and_capture(
        "--user-agent", "TestAgent/2.0", "--header", "Accept: application/json"
    )
    assert d.get("headers", {}).get("User-Agent") == "TestAgent/2.0"
    assert d.get("headers", {}).get("Accept") == "application/json"


def test_header_names_are_normalized_before_merge() -> None:
    d = _run_and_capture("--user-agent", "UA", "--header", "user-agent: CLI")
    assert d.get("headers", {}) == {"User-Agent": "CLI"}


def test_no_header_flags_omits_headers_key() -> None:
    assert "headers" not in _run_and_capture()


def test_auto_profile_fallback_defaults_true() -> None:
    assert _run_and_capture().get("auto_profile_fallback") is True


def test_auto_profile_fallback_can_be_disabled() -> None:
    assert (
        _run_and_capture("--auto_profile_fallback", "false").get(
            "auto_profile_fallback"
        )
        is False
    )


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
    d = _run_and_capture(
        "-c", "/tmp/cookies.txt", "-p", "socks5://127.0.0.1:1080"
    )
    assert d.get("cookies") == "/tmp/cookies.txt"
    assert d.get("proxy") == "socks5://127.0.0.1:1080"


def test_metadata_flags_are_added_when_not_explicitly_declared() -> None:
    from chat_downloader import cli as cli_module

    original = cli_module._build_field_info

    def fake_build_field_info(dc_class):
        info = original(dc_class)
        if dc_class is DownloaderConfig:
            info["connect_timeout"]["flags"] = ["-T"]
        return info

    with (
        patch(
            "chat_downloader.cli._build_field_info",
            side_effect=fake_build_field_info,
        ),
        patch("chat_downloader.cli.run") as mock_run,
    ):
        main(["https://example.com/watch?v=fake", "-T", "12.5"])

    assert mock_run.call_args.kwargs["connect_timeout"] == 12.5


def test_header_value_with_colon_preserves_full_value() -> None:
    d = _run_and_capture("--header", "Authorization: Bearer tok:en")
    assert d.get("headers", {}).get("Authorization") == "Bearer tok:en"


def test_invalid_header_flag_raises_parse_error() -> None:
    with pytest.raises(SystemExit):
        main(["https://example.com/watch?v=fake", "--header", "BrokenHeader"])


# ---------------------------------------------------------------------------
# parse_header()
# ---------------------------------------------------------------------------


def test_parse_header_returns_key_value_pair() -> None:
    assert parse_header("X-Test: value") == ("X-Test", "value")


def test_parse_header_rejects_missing_separator() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_header("BrokenHeader")


def test_parse_header_rejects_newlines() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="newline"):
        parse_header("X-Test: hello\r\nInjected: nope")


def test_parse_header_rejects_invalid_header_name() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid header name"):
        parse_header("Bad Header: value")


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
        "message_receive_timeout",
        "buffer_size",
        "output",
        "overwrite",
        "sort_keys",
    }
)


def test_all_cli_params_are_chat_request_fields() -> None:
    cr_fields = {f.name for f in dataclasses.fields(ChatRequest)}
    missing = _CLI_CHAT_PARAMS - cr_fields
    assert missing == set(), f"CLI params absent from ChatRequest: {missing}"


def test_all_chat_request_fields_are_in_cli() -> None:
    cr_fields = {f.name for f in dataclasses.fields(ChatRequest)}
    uncovered = cr_fields - _CLI_CHAT_PARAMS
    assert uncovered == set(), (
        f"ChatRequest fields missing from CLI: {uncovered}"
    )


def test_cli_chat_params_match_expected_legacy_keys() -> None:
    legacy_keys = set(ChatRequest(url="").as_dict().keys())
    assert _CLI_CHAT_PARAMS == legacy_keys


def test_cli_registration_fails_fast_without_dataclass_metadata(
    monkeypatch,
) -> None:
    original_build_field_info = cli_module._build_field_info

    def fake_build_field_info(dc_class):
        info = original_build_field_info(dc_class)
        if dc_class is ChatRequest:
            info.pop("url")
        return info

    monkeypatch.setattr(
        "chat_downloader.cli._build_field_info",
        fake_build_field_info,
    )

    with pytest.raises(
        RuntimeError, match="no matching dataclass CLI metadata"
    ):
        main(["https://example.com/watch?v=fake"])


def test_run_config_cli_flags_match_metadata() -> None:
    cli_fields = {
        f.name for f in dataclasses.fields(RunConfig) if f.metadata.get("cli")
    }
    assert cli_fields == {"quiet", "pause_on_debug", "exit_on_debug"}


def test_parse_header_empty_key_raises() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="NAME:VALUE"):
        parse_header(":somevalue")  # key="" after strip → raises

# SPDX-License-Identifier: MIT

"""Unit tests for src/chat_downloader/debugging.py to improve coverage."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg


@pytest.fixture(autouse=True)
def _restore_testing_mode():
    original = dbg.TESTING_MODE
    yield
    dbg.set_testing_mode(original)


@pytest.fixture(autouse=True)
def _restore_loggers():
    yield
    for configured_logger in dbg.loggers:
        configured_logger.disabled = False


@pytest.fixture(autouse=True)
def _restore_logger_levels():
    original_levels = [logger.level for logger in dbg.loggers]
    yield
    for logger, original_level in zip(
        dbg.loggers, original_levels, strict=True
    ):
        logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# log()
# ---------------------------------------------------------------------------


def test_log_valid_level() -> None:
    """Log() with a known level should not raise."""
    dbg.log("debug", "test message")
    dbg.log("info", "test message")
    dbg.log("warning", "test message")


def test_log_invalid_level_is_silently_ignored() -> None:
    """Log() with an unknown level does nothing (logger has no such method)."""
    dbg.log("nonexistent_level", "test message")  # should not raise


def test_log_with_list_of_items() -> None:
    """Log() accepts a list and logs each item."""
    dbg.log("debug", ["item1", "item2", "item3"])


def test_log_to_exit_raises_testing_exception_in_exit_on_debug_mode() -> None:
    """TestingException is raised when to_exit=True and mode=EXIT_ON_DEBUG."""
    dbg.set_testing_mode(dbg.TestingModes.EXIT_ON_DEBUG)
    with pytest.raises(dbg.TestingException):
        dbg.log("debug", "trigger exit", to_exit=True)


def test_log_to_exit_raises_in_exit_on_error_mode() -> None:
    """TestingException is raised when to_exit=True and mode=EXIT_ON_ERROR."""
    dbg.set_testing_mode(dbg.TestingModes.EXIT_ON_ERROR)
    with pytest.raises(dbg.TestingException):
        dbg.log("debug", "trigger exit", to_exit=True)


def test_log_to_exit_does_not_raise_in_none_mode() -> None:
    """to_exit=True is harmless when mode=NONE."""
    dbg.set_testing_mode(dbg.TestingModes.NONE)
    dbg.log("debug", "no exit", to_exit=True)  # should not raise


def test_log_to_pause_calls_pause_in_pause_on_debug_mode() -> None:
    """Line 56: pause() is called when to_pause=True and mode=PAUSE_ON_DEBUG."""
    dbg.set_testing_mode(dbg.TestingModes.PAUSE_ON_DEBUG)
    with patch("chat_downloader.debugging.pause") as mock_pause:
        dbg.log("debug", "trigger pause", to_pause=True)
    mock_pause.assert_called_once()


def test_log_to_pause_calls_pause_in_pause_on_error_mode() -> None:
    """Line 56: pause() is called when to_pause=True and mode=PAUSE_ON_ERROR."""
    dbg.set_testing_mode(dbg.TestingModes.PAUSE_ON_ERROR)
    with patch("chat_downloader.debugging.pause") as mock_pause:
        dbg.log("debug", "trigger pause", to_pause=True)
    mock_pause.assert_called_once()


def test_log_to_pause_does_not_call_pause_in_none_mode() -> None:
    """to_pause=True is harmless when mode=NONE."""
    dbg.set_testing_mode(dbg.TestingModes.NONE)
    with patch("chat_downloader.debugging.pause") as mock_pause:
        dbg.log("debug", "no pause", to_pause=True)
    mock_pause.assert_not_called()


# ---------------------------------------------------------------------------
# disable_logger()
# ---------------------------------------------------------------------------


def test_disable_logger_sets_disabled_flag() -> None:
    """Line 158: disable_logger() disables every configured logger."""
    dbg.disable_logger()
    assert all(configured_logger.disabled for configured_logger in dbg.loggers)


# ---------------------------------------------------------------------------
# sanitize_for_log()
# ---------------------------------------------------------------------------


def test_redacts_sensitive_init_fields_and_header_values() -> None:
    assert dbg.sanitize_for_log(
        {
            "headers": {
                "Authorization": "Bearer secret-token",
                "User-Agent": "TestAgent/1.0",
            },
            "proxy": "http://user:pass@example.invalid:8080",
            "cookies": "/tmp/cookies.txt",
            "connect_timeout": 10.0,
        }
    ) == {
        "headers": {
            "Authorization": dbg.REDACTED,
            "User-Agent": "TestAgent/1.0",
        },
        "proxy": dbg.REDACTED,
        "cookies": dbg.REDACTED,
        "connect_timeout": 10.0,
    }


def test_non_sensitive_headers_are_not_redacted() -> None:
    result = dbg.sanitize_for_log(
        {
            "headers": {
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "Authorization": "Bearer tok",
                "Cookie": "sid=abc",
            }
        }
    )
    assert result["headers"]["Content-Type"] == "application/json"
    assert result["headers"]["Accept"] == "*/*"
    assert result["headers"]["Accept-Language"] == "en-US"
    assert result["headers"]["Authorization"] == dbg.REDACTED
    assert result["headers"]["Cookie"] == dbg.REDACTED


def test_redacts_nested_sensitive_keys_in_sequences() -> None:
    assert dbg.sanitize_for_log(
        ({"authorization": "Bearer token"}, [{"cookie": "sid=abc"}])
    ) == ({"authorization": dbg.REDACTED}, [{"cookie": dbg.REDACTED}])


# ---------------------------------------------------------------------------
# capture_debug_sample()
# ---------------------------------------------------------------------------


def test_capture_debug_sample_writes_sanitized_json_deterministically() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path1 = dbg.capture_debug_sample(
            "Unknown continuation: heartbeat",
            {
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
                "value": 7,
            },
        )
        path2 = dbg.capture_debug_sample(
            "Unknown continuation: heartbeat",
            {
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
                "value": 7,
            },
        )

        assert path1 == path2
        assert path1 is not None
        with open(path1, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data == {
            "authorization": dbg.REDACTED,
            "headers": {"Authorization": dbg.REDACTED},
            "value": 7,
        }


def test_capture_debug_sample_logs_fixture_hint() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        with patch.object(dbg.logger, "debug") as mock_debug:
            path = dbg.capture_debug_sample(
                "youtube-unknown-continuation-heartbeat",
                {"continuation_key": "heartbeat"},
            )

        assert path is not None
        mock_debug.assert_called_with(
            "Captured debug sample: "
            f"path={path} "
            "suggested_fixture_site=youtube "
            "suggested_fixture_group=continuations "
            "suggested_fixture_name=youtube-unknown-continuation-heartbeat",
        )


def test_capture_debug_sample_scrubs_inline_tokens_in_values() -> None:
    """Tokens inside string values are redacted even without a sensitive key."""
    synthetic_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    payload = {
        # Key not in allowlist -> the inline header value passes the key
        # check but must still be scrubbed by the regex pass.
        "log_line": (
            f"Sent request with Authorization: Bearer {synthetic_jwt} "
            "and SAPISIDHASH 1234567890_abcdef0987654321deadbeef"
        ),
        "ok": "hello world",
    }

    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = dbg.capture_debug_sample("inline-secret", payload)
        assert path is not None
        with open(path, encoding="utf-8") as fh:
            contents = fh.read()

    # The literal secret bytes must not appear anywhere in the sample.
    assert synthetic_jwt not in contents
    assert "1234567890_abcdef0987654321deadbeef" not in contents
    # Untouched values survive.
    assert "hello world" in contents


def test_capture_debug_sample_is_disabled_without_env_flag() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {"CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir},
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = dbg.capture_debug_sample("label", {"value": 1})
        assert path is None
        assert os.listdir(temp_dir) == []


def test_capture_debug_sample_digest_is_fips_safe() -> None:
    """sha1 must use usedforsecurity=False for FIPS-enabled Python 3.12+."""
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = dbg.capture_debug_sample("fips_probe", {"x": 1})
        assert path is not None
        assert path.endswith(".json")


# ---------------------------------------------------------------------------
# supports_colour()
# ---------------------------------------------------------------------------


def test_returns_false_when_not_a_tty() -> None:
    """supports_colour() returns False when stdout is not a TTY."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: False})()
    with patch("sys.stdout", mock_stdout):
        result = dbg.supports_colour()
    assert not result


def test_returns_false_when_stdout_has_no_isatty() -> None:
    """supports_colour() returns False when stdout lacks isatty."""
    mock_stdout = object()  # no isatty attribute
    with patch("sys.stdout", mock_stdout):
        result = dbg.supports_colour()
    assert not result


def test_returns_true_on_non_windows_tty() -> None:
    """supports_colour() returns True on non-Windows TTY."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: True})()
    with patch("sys.stdout", mock_stdout), patch("sys.platform", "linux"):
        result = dbg.supports_colour()
    assert result


def test_returns_true_on_windows_with_colorama() -> None:
    """supports_colour() returns True on Windows when HAS_COLORAMA is True."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: True})()
    with (
        patch("sys.stdout", mock_stdout),
        patch("sys.platform", "win32"),
        patch.object(dbg, "HAS_COLORAMA", True),
    ):
        result = dbg.supports_colour()
    assert result


def test_returns_true_on_windows_with_ansicon() -> None:
    """supports_colour() returns True on Windows with ANSICON env var."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: True})()
    env = {"ANSICON": "1"}
    with (
        patch("sys.stdout", mock_stdout),
        patch("sys.platform", "win32"),
        patch("os.environ", env),
        patch.object(dbg, "HAS_COLORAMA", False),
    ):
        result = dbg.supports_colour()
    assert result


def test_returns_true_on_windows_terminal() -> None:
    """supports_colour() returns True when WT_SESSION is set."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: True})()
    env = {"WT_SESSION": "some-guid"}
    with (
        patch("sys.stdout", mock_stdout),
        patch("sys.platform", "win32"),
        patch("os.environ", env),
        patch.object(dbg, "HAS_COLORAMA", False),
    ):
        result = dbg.supports_colour()
    assert result


def test_returns_true_on_vscode_terminal() -> None:
    """supports_colour() returns True with TERM_PROGRAM=vscode."""
    mock_stdout = type("MockStdout", (), {"isatty": lambda self: True})()
    env = {"TERM_PROGRAM": "vscode"}
    with (
        patch("sys.stdout", mock_stdout),
        patch("sys.platform", "win32"),
        patch("os.environ", env),
        patch.object(dbg, "HAS_COLORAMA", False),
    ):
        result = dbg.supports_colour()
    assert result


def test_capture_debug_sample_oserror_returns_none(
    tmp_path, monkeypatch
) -> None:
    import logging
    from unittest.mock import patch as stdlib_patch

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "true")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(tmp_path))

    original_level = dbg.logger.level
    dbg.logger.setLevel(logging.DEBUG)
    try:
        with stdlib_patch(
            "chat_downloader.debugging.Path.mkdir",
            side_effect=OSError("disk full"),
        ):
            result = dbg.capture_debug_sample("test-label", {"key": "value"})
        assert result is None
    finally:
        dbg.logger.setLevel(original_level)

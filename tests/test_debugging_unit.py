# SPDX-License-Identifier: MIT

"""Unit tests for src/chat_downloader/debugging.py to improve coverage."""

from __future__ import annotations

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
    for logger, original_level in zip(dbg.loggers, original_levels, strict=True):
        logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# set_log_level() / loggers
# ---------------------------------------------------------------------------


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
    """disable_logger() disables every configured logger."""
    dbg.disable_logger()
    assert all(configured_logger.disabled for configured_logger in dbg.loggers)


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

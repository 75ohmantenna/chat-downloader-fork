# SPDX-License-Identifier: MIT

"""Behavioral tests for logging controls and terminal colour detection."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg
from tests.core_third_helpers import restore_loggers


@pytest.fixture(autouse=True)
def _restore_logging_state():
    with restore_loggers():
        yield


@pytest.mark.parametrize("level", ["debug", "info", "warning", "nonexistent_level"])
@pytest.mark.parametrize("items", ["message", ["first", "second"], ("first", "second")])
def test_log_emits_each_item_at_supported_levels(caplog, level, items):
    dbg.set_log_level("debug")
    with caplog.at_level(logging.DEBUG, logger=dbg.logger.name):
        dbg.log(level, items)
    expected = (
        []
        if level == "nonexistent_level"
        else ([items] if isinstance(items, str) else list(items))
    )
    assert [record.getMessage() for record in caplog.records] == expected


@pytest.mark.parametrize("mode_name", [mode.name for mode in dbg.TestingModes])
@pytest.mark.parametrize("enabled", [False, True])
def test_log_testing_controls(mode_name, enabled):
    mode = dbg.TestingModes[mode_name]
    dbg.set_testing_mode(mode)
    exits = enabled and mode_name.startswith("EXIT_")
    pauses = enabled and mode_name.startswith("PAUSE_")
    with patch.object(dbg, "pause") as pause:
        with pytest.raises(dbg.TestingException) if exits else nullcontext():
            dbg.log("debug", "trigger", to_exit=enabled, to_pause=enabled)
        assert pause.call_count == int(pauses)


def test_disable_logger_suppresses_output(caplog):
    dbg.set_log_level("debug")
    dbg.disable_logger()
    for logger in dbg.loggers:
        logger.error("must not be emitted")
    assert caplog.records == []


def test_console_handlers_install_only_when_cli_requests_them():
    assert dbg.handler not in dbg.logger.handlers
    assert dbg.handler not in logging.getLogger("urllib3").handlers
    dbg.install_cli_log_handler()
    dbg.install_cli_log_handler()
    assert dbg.logger.handlers.count(dbg.handler) == 1
    assert logging.getLogger("urllib3").handlers.count(dbg.handler) == 1


@pytest.mark.parametrize(
    ("tty", "platform", "colorama", "environment", "expected"),
    [
        (False, "linux", False, {}, False),
        (None, "linux", False, {}, False),
        (True, "linux", False, {}, True),
        (True, "win32", True, {}, True),
        (True, "win32", False, {"ANSICON": "1"}, True),
        (True, "win32", False, {"WT_SESSION": "guid"}, True),
        (True, "win32", False, {"TERM_PROGRAM": "vscode"}, True),
    ],
)
def test_supports_colour(monkeypatch, tty, platform, colorama, environment, expected):
    stdout = object() if tty is None else SimpleNamespace(isatty=lambda: tty)
    monkeypatch.setattr(dbg.sys, "stdout", stdout)
    monkeypatch.setattr(dbg.sys, "platform", platform)
    monkeypatch.setattr(dbg, "HAS_COLORAMA", colorama)
    monkeypatch.setattr(dbg.os, "environ", environment)
    assert dbg.supports_colour() is expected

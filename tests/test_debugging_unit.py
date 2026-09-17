# SPDX-License-Identifier: MIT

"""Behavioral tests for logging controls and terminal colour detection."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg


@pytest.fixture(autouse=True)
def _restore_logging_state():
    mode = dbg.get_testing_mode()
    state = [(logger, logger.level, logger.disabled) for logger in dbg.loggers]
    yield
    dbg.set_testing_mode(mode)
    for logger, level, disabled in state:
        logger.setLevel(level)
        logger.disabled = disabled


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
    exits = enabled and mode in (
        dbg.TestingModes.EXIT_ON_DEBUG,
        dbg.TestingModes.EXIT_ON_ERROR,
    )
    pauses = enabled and mode in (
        dbg.TestingModes.PAUSE_ON_DEBUG,
        dbg.TestingModes.PAUSE_ON_ERROR,
    )
    with patch.object(dbg, "pause") as pause:
        if exits:
            with pytest.raises(dbg.TestingException):
                dbg.log("debug", "trigger", to_exit=enabled, to_pause=enabled)
        else:
            dbg.log("debug", "trigger", to_exit=enabled, to_pause=enabled)
        assert pause.call_count == int(pauses)


def test_disable_logger_suppresses_output(caplog):
    dbg.set_log_level("debug")
    dbg.disable_logger()
    for logger in dbg.loggers:
        logger.error("must not be emitted")
    assert caplog.records == []


@pytest.mark.parametrize(
    ("stdout", "platform", "colorama", "environment", "expected"),
    [
        (SimpleNamespace(isatty=lambda: False), "linux", False, {}, False),
        (object(), "linux", False, {}, False),
        (SimpleNamespace(isatty=lambda: True), "linux", False, {}, True),
        (SimpleNamespace(isatty=lambda: True), "win32", True, {}, True),
        (SimpleNamespace(isatty=lambda: True), "win32", False, {"ANSICON": "1"}, True),
        (
            SimpleNamespace(isatty=lambda: True),
            "win32",
            False,
            {"WT_SESSION": "guid"},
            True,
        ),
        (
            SimpleNamespace(isatty=lambda: True),
            "win32",
            False,
            {"TERM_PROGRAM": "vscode"},
            True,
        ),
    ],
)
def test_supports_colour(
    monkeypatch, stdout, platform, colorama, environment, expected
):
    monkeypatch.setattr(dbg.sys, "stdout", stdout)
    monkeypatch.setattr(dbg.sys, "platform", platform)
    monkeypatch.setattr(dbg, "HAS_COLORAMA", colorama)
    monkeypatch.setattr(dbg.os, "environ", environment)
    assert dbg.supports_colour() is expected

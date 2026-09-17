# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib
import io
import logging
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import chat_downloader.debugging as dbg
from tests.core_third_helpers import restore_loggers


@pytest.fixture
def logging_state():
    with restore_loggers():
        yield


@contextmanager
def _reload_debugging(monkeypatch, *, colorama, tty=False):
    stream = io.StringIO()
    stream.isatty = lambda: tty
    with restore_loggers(), monkeypatch.context() as isolated:
        isolated.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: tty))
        isolated.setattr(sys, "stderr", stream)
        isolated.setattr(
            dbg.os,
            "environ",
            {
                k: v
                for k, v in dbg.os.environ.items()
                if k not in {"NO_COLOR", "FORCE_COLOR"}
            },
        )
        isolated.setitem(sys.modules, "colorama", colorama)
        try:
            module = importlib.reload(dbg)
            module.set_log_level("debug")
            yield module, stream
        finally:
            isolated.undo()
            importlib.reload(dbg)


@pytest.mark.parametrize("registry", ["enabled", "missing-value", "missing-module"])
def test_supports_colour_windows_registry(monkeypatch, registry):
    def query_value_ex(_key, _name):
        if registry == "missing-value":
            raise FileNotFoundError
        return (1, 0)

    monkeypatch.setattr(dbg.sys, "platform", "win32")
    monkeypatch.setattr(dbg, "HAS_COLORAMA", False)
    monkeypatch.setattr(dbg.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(dbg.os, "environ", {})
    monkeypatch.setitem(
        sys.modules,
        "winreg",
        None
        if registry == "missing-module"
        else SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            OpenKey=lambda root, key: (root, key),
            QueryValueEx=query_value_ex,
        ),
    )
    assert dbg.supports_colour() is (registry == "enabled")


@pytest.mark.parametrize("colour", [False, True])
def test_debugging_import_renders_with_available_colour(monkeypatch, colour):
    def init():
        if not colour:
            raise OSError("boom")

    # Exercise the real formatter and stream rather than pinning constructor calls.
    with _reload_debugging(
        monkeypatch, colorama=SimpleNamespace(init=init), tty=colour
    ) as (module, stream):
        module.log("warning", "visible token=PRIVATE")
        output = stream.getvalue()
        assert "visible" in output
        assert "PRIVATE" not in output
        assert ("\x1b[" in output) is colour
        assert module.supports_colour() is colour


def test_debug_log_applies_testing_controls(caplog, logging_state):
    dbg.set_log_level("debug")
    dbg.set_testing_mode(dbg.TestingModes.EXIT_ON_DEBUG)
    with (
        caplog.at_level(logging.DEBUG, logger=dbg.logger.name),
        pytest.raises(dbg.TestingException),
    ):
        dbg.debug_log("first", "second")
    assert [record.getMessage() for record in caplog.records] == ["first", "second"]


def test_set_log_level_filters_all_configured_loggers(caplog, logging_state):
    dbg.set_log_level("error")
    for logger in dbg.loggers:
        logger.warning("hidden")
        logger.error("visible")
    assert [(record.name, record.getMessage()) for record in caplog.records] == [
        (logger.name, "visible") for logger in dbg.loggers
    ]

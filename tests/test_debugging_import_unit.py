# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib
import logging
import sys
from types import SimpleNamespace
from typing import NoReturn

import chat_downloader.debugging as dbg


class _FakeTTY:
    def isatty(self) -> bool:
        return True


def _reload_debugging(
    monkeypatch,
    *,
    stdout=None,
    colorama_module=None,
    colorlog_module=None,
):
    original_stdout = sys.stdout
    original_colorama = sys.modules.get("colorama")
    original_colorlog = sys.modules.get("colorlog")
    original_handlers = {logger: list(logger.handlers) for logger in dbg.loggers}

    if stdout is not None:
        monkeypatch.setattr(sys, "stdout", stdout)

    if colorama_module is None:
        sys.modules.pop("colorama", None)
    else:
        sys.modules["colorama"] = colorama_module

    if colorlog_module is None:
        sys.modules.pop("colorlog", None)
    else:
        sys.modules["colorlog"] = colorlog_module

    module = importlib.reload(dbg)
    return (
        module,
        original_stdout,
        original_colorama,
        original_colorlog,
        original_handlers,
    )


def _restore_debugging(
    original_stdout, original_colorama, original_colorlog, original_handlers
) -> None:
    sys.stdout = original_stdout

    if original_colorama is None:
        sys.modules.pop("colorama", None)
    else:
        sys.modules["colorama"] = original_colorama

    if original_colorlog is None:
        sys.modules.pop("colorlog", None)
    else:
        sys.modules["colorlog"] = original_colorlog

    module = importlib.reload(dbg)
    for logger, handlers in original_handlers.items():
        logger.handlers = handlers
    module.logger.disabled = False


def test_supports_colour_uses_windows_registry_when_enabled(
    monkeypatch,
) -> None:
    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda root, key: (root, key),
        QueryValueEx=lambda key, name: (1, 0),
    )

    monkeypatch.setattr(dbg.sys, "platform", "win32")
    monkeypatch.setattr(dbg, "HAS_COLORAMA", False)
    monkeypatch.setattr(dbg.sys, "stdout", _FakeTTY())
    monkeypatch.setattr(dbg.os, "environ", {})
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert dbg.supports_colour() is True


def test_supports_colour_registry_missing_value_returns_false(
    monkeypatch,
) -> None:
    def query_value_ex(_key, _name) -> NoReturn:
        raise FileNotFoundError

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda root, key: (root, key),
        QueryValueEx=query_value_ex,
    )

    monkeypatch.setattr(dbg.sys, "platform", "win32")
    monkeypatch.setattr(dbg, "HAS_COLORAMA", False)
    monkeypatch.setattr(dbg.sys, "stdout", _FakeTTY())
    monkeypatch.setattr(dbg.os, "environ", {})
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert dbg.supports_colour() is False


def test_supports_colour_registry_import_error_returns_false(
    monkeypatch,
) -> None:
    monkeypatch.setattr(dbg.sys, "platform", "win32")
    monkeypatch.setattr(dbg, "HAS_COLORAMA", False)
    monkeypatch.setattr(dbg.sys, "stdout", _FakeTTY())
    monkeypatch.setattr(dbg.os, "environ", {})
    monkeypatch.delitem(sys.modules, "winreg", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "winreg":
            raise ImportError
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert dbg.supports_colour() is False


def test_debugging_import_handles_colorama_init_oserror(monkeypatch) -> None:
    fake_colorama = SimpleNamespace(init=lambda: (_ for _ in ()).throw(OSError("boom")))

    (
        module,
        original_stdout,
        original_colorama,
        original_colorlog,
        original_handlers,
    ) = _reload_debugging(
        monkeypatch,
        colorama_module=fake_colorama,
        colorlog_module=None,
    )

    try:
        assert module.HAS_COLORAMA is False
        assert isinstance(module.handler, logging.StreamHandler)
    finally:
        _restore_debugging(
            original_stdout,
            original_colorama,
            original_colorlog,
            original_handlers,
        )


def test_debugging_import_uses_colorlog_when_colour_supported(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeColoredFormatter:
        def __init__(self, fmt, log_colors) -> None:
            formatter_calls.append((fmt, log_colors))

    class FakeStreamHandler(logging.StreamHandler):
        def __init__(self) -> None:
            super().__init__()
            self.formatter = None

        def setFormatter(self, formatter) -> None:
            self.formatter = formatter

    fake_colorlog = SimpleNamespace(
        StreamHandler=FakeStreamHandler,
        ColoredFormatter=FakeColoredFormatter,
        getLogger=logging.getLogger,
    )
    fake_colorama = SimpleNamespace(init=lambda: None)

    (
        module,
        original_stdout,
        original_colorama,
        original_colorlog,
        original_handlers,
    ) = _reload_debugging(
        monkeypatch,
        stdout=_FakeTTY(),
        colorama_module=fake_colorama,
        colorlog_module=fake_colorlog,
    )

    try:
        assert module.HAS_COLORAMA is True
        assert isinstance(module.handler, FakeStreamHandler)
        assert formatter_calls == [
            (
                "[%(log_color)s%(levelname)s%(reset)s] %(message)s",
                {
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            ),
        ]
    finally:
        _restore_debugging(
            original_stdout,
            original_colorama,
            original_colorlog,
            original_handlers,
        )


def test_debug_log_delegates_to_log(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(dbg, "log", lambda *args: calls.append(args))

    dbg.debug_log("first", "second")

    assert calls == [("debug", ("first", "second"), True, True)]


def test_set_log_level_updates_all_configured_loggers() -> None:
    original_levels = [logger.level for logger in dbg.loggers]

    try:
        dbg.set_log_level("error")
        assert [logger.level for logger in dbg.loggers] == [logging.ERROR] * len(
            dbg.loggers,
        )
    finally:
        for logger, original_level in zip(dbg.loggers, original_levels, strict=True):
            logger.setLevel(original_level)

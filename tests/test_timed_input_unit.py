# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib
import io
import sys
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from chat_downloader.utils import timed_input as timed_utils
from chat_downloader.utils.timed_generator import TimedGenerator
from chat_downloader.utils.timed_input import TimeoutOccurred


def _reload_timed_utils_with_msvcrt(fake_msvcrt: Any):
    original = sys.modules.get("msvcrt")
    sys.modules["msvcrt"] = fake_msvcrt
    module = importlib.reload(timed_utils)
    return module, original


def _restore_timed_utils(original_msvcrt: Any) -> None:
    if original_msvcrt is None:
        sys.modules.pop("msvcrt", None)
    else:
        sys.modules["msvcrt"] = original_msvcrt
    importlib.reload(timed_utils)


def test_timed_input_without_timeout_uses_builtin_input(monkeypatch) -> None:
    prompts = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "typed value"

    monkeypatch.setattr("builtins.input", fake_input)

    assert timed_utils.timed_input(timeout=None, prompt="Enter value:") == "typed value"
    assert prompts == ["Enter value:"]


def test_timed_input_returns_default_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        timed_utils,
        "_timed_input",
        lambda *_args: (_ for _ in ()).throw(TimeoutOccurred()),
    )

    assert timed_utils.timed_input(timeout=0.5, default="fallback") == "fallback"


def test_posix_timed_input_returns_line_when_selector_has_event(
    monkeypatch,
) -> None:
    outputs: list[str] = []

    class FakeSelector:
        def register(self, *_args) -> None:
            return None

        def select(self, timeout):
            assert timeout == 1.5
            return [
                (
                    SimpleNamespace(
                        fileobj=SimpleNamespace(readline=lambda: "hello world\n"),
                    ),
                    None,
                ),
            ]

    monkeypatch.setattr(timed_utils, "echo", outputs.append)
    monkeypatch.setattr(
        timed_utils.selectors,
        "DefaultSelector",
        FakeSelector,
    )

    assert (
        timed_utils.posix_timed_input(1.5, "Prompt: ", newline=False) == "hello world"
    )
    assert outputs == ["Prompt: "]


def test_posix_timed_input_register_failure_raises_timeout_and_prints_newline(
    monkeypatch,
) -> None:
    outputs: list[str] = []

    class FakeSelector:
        def register(self, *_args) -> NoReturn:
            raise io.UnsupportedOperation

    monkeypatch.setattr(timed_utils, "echo", outputs.append)
    monkeypatch.setattr(
        timed_utils.selectors,
        "DefaultSelector",
        FakeSelector,
    )

    with pytest.raises(TimeoutOccurred):
        timed_utils.posix_timed_input(1.0, "Prompt: ", newline=True)

    assert outputs == ["Prompt: ", timed_utils.LF]


def test_posix_timed_input_timeout_flushes_stdin(monkeypatch) -> None:
    outputs: list[str] = []
    tcflush_calls = []

    class FakeSelector:
        def register(self, *_args) -> None:
            return None

        def select(self, _timeout):
            return []

    monkeypatch.setattr(timed_utils, "echo", outputs.append)
    monkeypatch.setattr(
        timed_utils.selectors,
        "DefaultSelector",
        FakeSelector,
    )
    monkeypatch.setattr(
        timed_utils.termios,
        "tcflush",
        lambda stream, mode: tcflush_calls.append((stream, mode)),
    )

    with pytest.raises(TimeoutOccurred):
        timed_utils.posix_timed_input(1.0, "Prompt: ", newline=True)

    assert outputs == ["Prompt: ", timed_utils.LF]
    assert tcflush_calls == [(timed_utils.sys.stdin, timed_utils.termios.TCIFLUSH)]


def test_posix_timed_input_timeout_ignores_tcflush_errors(monkeypatch) -> None:
    class FakeSelector:
        def register(self, *_args) -> None:
            return None

        def select(self, _timeout):
            return []

    monkeypatch.setattr(
        timed_utils.selectors,
        "DefaultSelector",
        FakeSelector,
    )
    monkeypatch.setattr(
        timed_utils.termios,
        "tcflush",
        lambda *_args: (_ for _ in ()).throw(OSError("unsupported")),
    )
    monkeypatch.setattr(timed_utils, "echo", lambda *_args: None)

    with pytest.raises(TimeoutOccurred):
        timed_utils.posix_timed_input(1.0, "Prompt: ", newline=False)


def test_win_timed_input_handles_backspace_and_newline(monkeypatch) -> None:
    outputs: list[str] = []
    chars = iter(["a", "b", "\b", "c", "\r"])
    fake_msvcrt = SimpleNamespace(
        kbhit=lambda: True,
        getwche=lambda: next(chars),
    )
    module, original_msvcrt = _reload_timed_utils_with_msvcrt(fake_msvcrt)

    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5])

    try:
        monkeypatch.setattr(module, "echo", outputs.append)
        monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

        assert module.win_timed_input(1.0, "P>", newline=False) == "ac"
        assert outputs == ["P>", "\r    \rP>a", module.CRLF]
    finally:
        _restore_timed_utils(original_msvcrt)


def test_win_timed_input_raises_keyboard_interrupt(monkeypatch) -> None:
    fake_msvcrt = SimpleNamespace(
        kbhit=lambda: True,
        getwche=lambda: "\003",
    )
    module, original_msvcrt = _reload_timed_utils_with_msvcrt(fake_msvcrt)

    try:
        monkeypatch.setattr(module, "echo", lambda *_args: None)
        monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

        with pytest.raises(KeyboardInterrupt):
            module.win_timed_input(1.0, "P>", newline=False)
    finally:
        _restore_timed_utils(original_msvcrt)


def test_win_timed_input_timeout_prints_newline(monkeypatch) -> None:
    outputs: list[str] = []
    fake_msvcrt = SimpleNamespace(
        kbhit=lambda: False,
        getwche=lambda: "",
    )
    module, original_msvcrt = _reload_timed_utils_with_msvcrt(fake_msvcrt)
    monotonic_values = iter([0.0, 0.0, 2.0])

    try:
        monkeypatch.setattr(module, "echo", outputs.append)
        monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))
        monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

        with pytest.raises(module.TimeoutOccurred):
            module.win_timed_input(1.0, "P>", newline=True)

        assert outputs == ["P>", module.CRLF]
    finally:
        _restore_timed_utils(original_msvcrt)


class _AliveTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def is_alive(self) -> bool:
        return True

    def cancel(self) -> None:
        self.cancelled = True


def test_timed_generator_keyboard_interrupt_with_active_timer_propagates() -> None:
    class InterruptingIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise KeyboardInterrupt

    tg = TimedGenerator(InterruptingIterator())
    fake_timer = _AliveTimer()
    tg.timer = cast("Any", fake_timer)

    with pytest.raises(KeyboardInterrupt):
        next(tg)

    assert fake_timer.cancelled is True

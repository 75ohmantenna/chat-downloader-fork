# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib
import io
import os
import sys
import threading
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from chat_downloader.utils import timed_input as timed_utils
from chat_downloader.utils.timed_generator import TimedGenerator
from chat_downloader.utils.timed_input import TimeoutOccurred


@pytest.fixture
def pipe_stdin(monkeypatch):
    reader, writer = os.pipe()
    with (
        os.fdopen(reader, encoding="utf-8") as stream,
        os.fdopen(writer, "wb", buffering=0) as output,
    ):
        monkeypatch.setattr(timed_utils.sys, "stdin", stream)
        yield stream, output


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
        lambda *_args, **_kw: (_ for _ in ()).throw(TimeoutOccurred()),
    )

    assert timed_utils.timed_input(timeout=0.5, default="fallback") == "fallback"


def test_posix_timed_input_returns_line_when_selector_has_event(
    monkeypatch,
) -> None:
    outputs: list[str] = []

    class FakeSelector:
        def close(self) -> None:
            pass

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
        def close(self) -> None:
            pass

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


def test_posix_timed_input_handles_regular_file_stdin(monkeypatch) -> None:
    with open(__file__, encoding="utf-8") as stream:
        monkeypatch.setattr(timed_utils.sys, "stdin", stream)
        monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
        with pytest.raises(TimeoutOccurred):
            timed_utils.posix_timed_input(0.01, "", newline=False)


def test_posix_timed_input_eof_waits_remaining_delay(monkeypatch) -> None:
    slept: list[float] = []

    class FakeSelector:
        def close(self) -> None:
            pass

        def register(self, *_args) -> None:
            return None

        def select(self, _timeout):
            return [
                (SimpleNamespace(fileobj=SimpleNamespace(readline=lambda: "")), None)
            ]

    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", FakeSelector)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    monkeypatch.setattr(timed_utils.time, "sleep", slept.append)
    with pytest.raises(TimeoutOccurred):
        timed_utils.posix_timed_input(1.0, "", newline=False)
    assert slept[0] > 0.9


def test_posix_timed_input_timeout_flushes_stdin(monkeypatch) -> None:
    outputs: list[str] = []
    tcflush_calls = []

    class FakeSelector:
        def close(self) -> None:
            pass

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
        def close(self) -> None:
            pass

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


@pytest.mark.parametrize("ready", [False, True])
def test_terminal_retry_cancellation_preserves_later_input(
    monkeypatch, ready, pipe_stdin
):
    from chat_downloader.utils.retry_utils import RetryPolicy
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    stream, writer = pipe_stdin
    flushed = []

    class Selector:
        def register(self, *_args):
            pass

        def select(self, timeout):
            writer.write(b"later\n")
            stopped.set()
            return [True] if ready else []

        def close(self):
            pass

    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    monkeypatch.setattr(
        timed_utils.termios, "tcflush", lambda *_args: flushed.append("later")
    )
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: True
    )
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy(retry_timeout=60).wait(1)
        assert stream.readline() == "later\n"
        assert flushed == []
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_zero_delay_retry_does_not_resume_cancelled_worker(monkeypatch):
    from chat_downloader.utils.retry_utils import RetryPolicy
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: False
    )
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy(retry_timeout=0).wait(1)
    finally:
        _POLLING_STOP_EVENT.reset(token)


@pytest.mark.parametrize("timeout", [0, 0.25])
def test_cancellable_terminal_timeout_flushes_only_once(
    monkeypatch, pipe_stdin, timeout
):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    clock, flushed, closed, waits = [0.0], [], [], []

    class Selector:
        def register(self, *_args):
            pass

        def select(self, interval):
            waits.append(interval)
            clock[0] += interval
            assert flushed == []
            return []

        def close(self):
            closed.append(True)

    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    monkeypatch.setattr(timed_utils.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        timed_utils.termios, "tcflush", lambda *_args: flushed.append(True)
    )
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        assert timed_utils.timed_input(timeout, default="expired") == "expired"
        assert waits == pytest.approx([0] if timeout == 0 else [0.1, 0.1, 0.05])
        assert flushed == [True]
        assert closed == [True]
    finally:
        _POLLING_STOP_EVENT.reset(token)


@pytest.mark.parametrize("registration_error", [False, True])
def test_manual_terminal_eof_closes_selector_and_reports_detachment(
    monkeypatch, registration_error, pipe_stdin
):
    from chat_downloader.errors import ChatDownloaderError
    from chat_downloader.utils.retry_utils import RetryPolicy
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    closed = []
    pipe_stdin[1].close()

    class Selector:
        def register(self, *_args):
            if registration_error:
                raise io.UnsupportedOperation

        def select(self, _interval):
            return [
                (SimpleNamespace(fileobj=SimpleNamespace(readline=lambda: "")), None)
            ]

        def close(self):
            closed.append(True)

    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: True
    )
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        with pytest.raises(ChatDownloaderError, match="input closed"):
            RetryPolicy(retry_timeout=-1).wait(1)
        assert closed == [True]
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_windows_cancellation_before_key_read_preserves_later_input(monkeypatch):
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped, consumed = threading.Event(), []

    def ready():
        stopped.set()
        return True

    module, original_msvcrt = _reload_timed_utils_with_msvcrt(
        SimpleNamespace(kbhit=ready, getwche=lambda: consumed.append("later") or "\r")
    )
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        monkeypatch.setattr(module, "echo", lambda _text: None)
        with pytest.raises(_PollingCancelled):
            module.timed_input(60)
        assert consumed == []
    finally:
        _POLLING_STOP_EVENT.reset(token)
        _restore_timed_utils(original_msvcrt)


def test_windows_manual_input_retains_characters_across_polls(monkeypatch):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    ready = iter([True, False, True, True])
    characters = iter(["a", "b", "\r"])
    stopped = threading.Event()
    module, original_msvcrt = _reload_timed_utils_with_msvcrt(
        SimpleNamespace(kbhit=lambda: next(ready), getwche=lambda: next(characters))
    )
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        monkeypatch.setattr(module, "echo", lambda _text: None)
        monkeypatch.setattr(stopped, "wait", lambda _seconds: False)
        assert module.timed_input() == "ab"
    finally:
        _POLLING_STOP_EVENT.reset(token)
        _restore_timed_utils(original_msvcrt)


def test_terminal_eof_remaining_wait_is_cancellable_without_flushing(
    monkeypatch, pipe_stdin
):
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    closed, flushed = [], []
    pipe_stdin[1].close()

    class Selector:
        def register(self, *_args):
            pass

        def select(self, _interval):
            return [
                (SimpleNamespace(fileobj=SimpleNamespace(readline=lambda: "")), None)
            ]

        def close(self):
            closed.append(True)

    def cancel_during_wait(_seconds):
        stopped.set()
        return True

    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    monkeypatch.setattr(stopped, "wait", cancel_during_wait)
    monkeypatch.setattr(
        timed_utils.termios, "tcflush", lambda *_args: flushed.append(True)
    )
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            timed_utils.timed_input(60)
        assert closed == [True]
        assert flushed == []
    finally:
        _POLLING_STOP_EVENT.reset(token)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal regression")
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("buffered", [False, True])
def test_terminal_partial_line_does_not_outlive_manual_retry_deadline(
    monkeypatch, raw, buffered
):
    import tty

    from chat_downloader.utils.retry_utils import RetryPolicy

    master, slave = os.openpty()
    if raw:
        tty.setraw(slave)
    entered, finalized = threading.Event(), threading.Event()

    class TerminalInput(io.TextIOWrapper):
        def read(self, size=-1):
            entered.set()
            return super().read(size)

        def readline(self, size=-1):
            entered.set()
            return super().readline(size)

    binary = io.FileIO(slave, mode="r")
    stream = TerminalInput(
        io.BufferedReader(binary) if buffered else binary, encoding="utf-8"
    )
    monkeypatch.setattr(timed_utils.sys, "stdin", stream)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    os.write(master, b"partial" if raw else b"partial\x04")

    def provider():
        try:
            RetryPolicy(retry_timeout=-1).wait(1)
            yield "late retry"
        finally:
            finalized.set()

    source = TimedGenerator(provider(), timeout=0.1)
    try:
        assert entered.wait(1)
        assert list(source) == []
        assert finalized.is_set()
        assert not source._worker.is_alive()
        assert os.get_blocking(slave)
    finally:
        os.write(master, b"\n")
        source.close()
        source._worker.join(1)
        stream.close()
        os.close(master)


def test_windows_manual_retry_reports_ctrl_z_as_closed_input(monkeypatch):
    from chat_downloader.errors import ChatDownloaderError
    from chat_downloader.utils.retry_utils import RetryPolicy
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    module, original_msvcrt = _reload_timed_utils_with_msvcrt(
        SimpleNamespace(kbhit=lambda: True, getwche=lambda: "\032")
    )
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: True
    )
    monkeypatch.setattr(module, "echo", lambda _text: None)
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        with pytest.raises(ChatDownloaderError, match="input closed"):
            RetryPolicy(retry_timeout=-1).wait(1)
    finally:
        _POLLING_STOP_EVENT.reset(token)
        _restore_timed_utils(original_msvcrt)


@pytest.mark.parametrize("timeout", [None, 1.0])
@pytest.mark.parametrize("errors", ["strict", "surrogateescape", "replace", "ignore"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_terminal_decodes_unicode_split_across_readiness_intervals(
    monkeypatch, timeout, errors, encoding
):
    import tty

    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    master, slave = os.openpty()
    tty.setraw(slave)
    encoded = "\U0001f600 line\n".encode(encoding)
    chunks = iter(bytes([byte]) for byte in encoded[1:])
    original_selector = timed_utils.selectors.DefaultSelector
    flushed = []

    class FragmentingSelector(original_selector):
        def select(self, interval=None):
            os.write(master, next(chunks))
            return super().select(interval)

    with os.fdopen(slave, encoding=encoding, errors=errors) as stream:
        monkeypatch.setattr(timed_utils.sys, "stdin", stream)
        monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
        monkeypatch.setattr(
            timed_utils.selectors, "DefaultSelector", FragmentingSelector
        )
        monkeypatch.setattr(
            timed_utils.termios, "tcflush", lambda *_args: flushed.append(True)
        )
        token = _POLLING_STOP_EVENT.set(threading.Event())
        try:
            os.write(master, encoded[:1])
            assert timed_utils.timed_input(timeout) == "\U0001f600 line"
            assert os.get_blocking(slave)
            assert flushed == []
        finally:
            _POLLING_STOP_EVENT.reset(token)
            os.close(master)


@pytest.mark.parametrize("timeout", [0, 1])
@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_cancellable_input_preserves_prebuffered_text_and_following_line(
    monkeypatch, pipe_stdin, timeout, blocking, override
):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    stream, writer = pipe_stdin
    writer.write(b"Pcached line\nfollowing line\n")
    assert stream.read(1) == "P"
    os.set_blocking(stream.fileno(), blocking)
    raw = stream.buffer.raw
    if override:
        original_readinto = raw.readinto
        raw.readinto = original_readinto
    original_attributes = vars(raw).copy()
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        assert timed_utils.timed_input(timeout) == "cached line"
        assert stream.readline() == "following line\n"
        assert os.get_blocking(stream.fileno()) == blocking
        assert vars(raw) == original_attributes
    finally:
        _POLLING_STOP_EVENT.reset(token)


@pytest.mark.parametrize("encoded", [b"\xff", b"\xf0", b"\xf0\x9f"])
def test_cancellable_input_preserves_genuine_decoding_errors(
    monkeypatch, pipe_stdin, encoded
):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    stream, writer = pipe_stdin
    writer.write(encoded)
    writer.close()
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        with pytest.raises(UnicodeDecodeError):
            timed_utils.timed_input(1)
        assert os.get_blocking(stream.fileno())
        assert "readinto" not in vars(stream.buffer.raw)
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_cancellable_input_returns_unterminated_text_at_true_eof(
    monkeypatch, pipe_stdin
):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    stream, writer = pipe_stdin
    writer.write(b"final partial line")
    writer.close()
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    token = _POLLING_STOP_EVENT.set(threading.Event())
    try:
        assert timed_utils.timed_input() == "final partial line"
        assert os.get_blocking(stream.fileno())
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_direct_posix_manual_input_preserves_eof(monkeypatch, pipe_stdin):
    pipe_stdin[1].close()
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    with pytest.raises(EOFError):
        timed_utils.posix_timed_input(None, "", newline=False)


@pytest.fixture
def overlapping_terminal(monkeypatch):
    import tty

    from chat_downloader.utils.retry_utils import RetryPolicy

    master, slave = os.openpty()
    tty.setraw(slave)
    readers = {"owner": threading.Event(), "waiter": threading.Event()}
    finished = {"owner": threading.Event(), "waiter": threading.Event()}
    identities, sources, prompts, flushed = {}, [], [], []
    untimed_threads = []
    blocked = threading.Event()
    waiting = threading.Event()
    original_lock = timed_utils._INPUT_LOCK
    original_selector = timed_utils.selectors.DefaultSelector
    owner_drained = threading.Event()

    class ObservedSelector(original_selector):
        def select(self, timeout=None):
            if identities.get(threading.get_ident()) == "owner":
                owner_drained.set()
            return super().select(timeout)

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            if kwargs.get("timeout") is not None:
                waiting.set()
            acquired = original_lock.acquire(*args, **kwargs)
            if not acquired and identities.get(threading.get_ident()) == "waiter":
                blocked.set()
            return acquired

        def release(self):
            original_lock.release()

    class TerminalInput(io.TextIOWrapper):
        def read(self, size=-1):
            readers[identities[threading.get_ident()]].set()
            return super().read(size)

        def readline(self, size=-1):
            readers[identities[threading.get_ident()]].set()
            return super().readline(size)

    stream = TerminalInput(
        io.BufferedReader(io.FileIO(slave, mode="r")), encoding="utf-8"
    )
    raw = stream.buffer.raw
    original_attributes = vars(raw).copy()
    monkeypatch.setattr(timed_utils.sys, "stdin", stream)
    monkeypatch.setattr(timed_utils, "_INPUT_LOCK", ObservedLock())
    monkeypatch.setattr(timed_utils.selectors, "DefaultSelector", ObservedSelector)
    monkeypatch.setattr(timed_utils, "echo", prompts.append)
    monkeypatch.setattr(
        timed_utils.termios, "tcflush", lambda *_args: flushed.append(True)
    )

    def start(label, input_timeout=None, *, timed=True):
        def provider():
            identities[threading.get_ident()] = label
            try:
                if input_timeout is None:
                    RetryPolicy(retry_timeout=-1).wait(1)
                    yield label
                else:
                    yield timed_utils.timed_input(
                        input_timeout, prompt=label, default="expired"
                    )
            finally:
                finished[label].set()

        if not timed:
            result, errors = [], []

            def consume():
                try:
                    result.extend(provider())
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=consume, daemon=True)
            untimed_threads.append(thread)
            thread.start()
            return SimpleNamespace(thread=thread, result=result, errors=errors)

        source = TimedGenerator(provider())
        sources.append(source)
        return source

    try:
        os.write(master, b"partial")
        yield SimpleNamespace(
            start=start,
            master=master,
            slave=slave,
            raw=raw,
            original_attributes=original_attributes,
            readers=readers,
            finished=finished,
            blocked=blocked,
            owner_drained=owner_drained,
            waiting=waiting,
            prompts=prompts,
            flushed=flushed,
        )
    finally:
        os.write(master, b"\n")
        for source in sources:
            source.close()
        for source in sources:
            source._worker.join(1)
        for thread in untimed_threads:
            os.write(master, b"\n")
            thread.join(1)
        stream.close()
        os.close(master)


@pytest.mark.parametrize("waiter_finish", ["cancel", "enter", "finite_enter"])
def test_overlapping_manual_workers_transfer_stdin_ownership_safely(
    overlapping_terminal, waiter_finish
):
    terminal = overlapping_terminal
    first = terminal.start("owner")
    assert terminal.owner_drained.wait(1)
    second = terminal.start("waiter", 1 if waiter_finish == "finite_enter" else None)
    assert terminal.blocked.wait(1)
    assert not terminal.readers["waiter"].is_set()
    first.close()
    assert terminal.finished["owner"].is_set()
    assert terminal.readers["waiter"].wait(1)
    assert not os.get_blocking(terminal.slave)
    assert "readinto" in vars(terminal.raw)
    if waiter_finish == "cancel":
        second.close()
    else:
        os.write(terminal.master, b"next\n")
        expected = "next" if waiter_finish == "finite_enter" else "waiter"
        assert list(second) == [expected]
    assert terminal.finished["waiter"].is_set()
    assert not second._worker.is_alive()
    assert os.get_blocking(terminal.slave)
    assert vars(terminal.raw) == terminal.original_attributes
    assert terminal.flushed == []


@pytest.mark.parametrize("waiter_finish", ["cancel", "expire"])
def test_waiting_input_cancellation_or_expiry_leaves_current_owner_untouched(
    overlapping_terminal, waiter_finish
):
    terminal = overlapping_terminal
    first = terminal.start("owner")
    assert terminal.owner_drained.wait(1)
    active_attributes = vars(terminal.raw).copy()
    second = terminal.start("waiter", 0.02 if waiter_finish == "expire" else None)
    assert terminal.blocked.wait(1)
    if waiter_finish == "cancel":
        second.close()
    else:
        assert list(second) == ["expired"]
    assert terminal.finished["waiter"].is_set()
    assert not terminal.readers["waiter"].is_set()
    assert terminal.prompts == ["Press Enter to continue..."]
    assert terminal.flushed == []
    assert vars(terminal.raw) == active_attributes
    assert not os.get_blocking(terminal.slave)
    assert first._worker.is_alive()
    os.write(terminal.master, b"\n")
    assert list(first) == ["owner"]
    assert os.get_blocking(terminal.slave)
    assert vars(terminal.raw) == terminal.original_attributes


def test_cancellable_finite_eof_preserves_remaining_delay_and_returns_default(
    monkeypatch, pipe_stdin
):
    from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

    stream, writer = pipe_stdin
    writer.close()
    stopped = threading.Event()
    delays = []
    monkeypatch.setattr(stopped, "wait", lambda delay: delays.append(delay) or False)
    monkeypatch.setattr(timed_utils, "echo", lambda _text: None)
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        assert timed_utils.timed_input(1, default="expired") == "expired"
        assert len(delays) == 1
        assert 0.9 < delays[0] <= 1
        assert os.get_blocking(stream.fileno())
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_input_deadline_expiring_during_ownership_transfer_never_touches_stdin(
    monkeypatch, overlapping_terminal
):
    terminal = overlapping_terminal
    first = terminal.start("owner")
    assert terminal.owner_drained.wait(1)
    expired = threading.Event()
    original_monotonic = timed_utils.time.monotonic
    monkeypatch.setattr(
        timed_utils.time,
        "monotonic",
        lambda: original_monotonic() + (10 if expired.is_set() else 0),
    )
    original_acquire = timed_utils._INPUT_LOCK.acquire

    def acquire_at_deadline(*args, **kwargs):
        acquired = original_acquire(*args, **kwargs)
        if acquired and terminal.waiting.is_set():
            expired.set()
        return acquired

    monkeypatch.setattr(timed_utils._INPUT_LOCK, "acquire", acquire_at_deadline)
    second = terminal.start("waiter", 1)
    assert terminal.waiting.wait(1)
    first.close()
    assert list(second) == ["expired"]
    assert not terminal.readers["waiter"].is_set()
    assert terminal.prompts == ["Press Enter to continue..."]
    assert terminal.flushed == []
    assert os.get_blocking(terminal.slave)
    assert vars(terminal.raw) == terminal.original_attributes
    assert timed_utils._INPUT_LOCK.acquire(blocking=False)
    timed_utils._INPUT_LOCK.release()


def test_untimed_manual_retry_waits_for_timed_stdin_owner_without_interference(
    monkeypatch, overlapping_terminal
):
    import builtins

    terminal = overlapping_terminal
    first = terminal.start("owner")
    assert terminal.owner_drained.wait(1)
    active_attributes = vars(terminal.raw).copy()
    builtin_prompts = []
    original_input = builtins.input

    def input_after_ownership(prompt):
        builtin_prompts.append(prompt)
        return original_input(prompt)

    monkeypatch.setattr(builtins, "input", input_after_ownership)
    second = terminal.start("waiter", timed=False)
    assert terminal.blocked.wait(1)
    assert not terminal.readers["waiter"].is_set()
    assert builtin_prompts == []
    assert vars(terminal.raw) == active_attributes
    first.close()
    assert terminal.readers["waiter"].wait(1)
    os.write(terminal.master, b"untimed\n")
    second.thread.join(1)
    assert not second.thread.is_alive()
    assert second.errors == []
    assert second.result == ["waiter"]
    assert builtin_prompts == ["Press Enter to continue..."]
    assert terminal.flushed == []
    assert os.get_blocking(terminal.slave)
    assert vars(terminal.raw) == terminal.original_attributes

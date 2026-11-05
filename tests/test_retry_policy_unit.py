# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chat_downloader.errors import ChatDownloaderError
from chat_downloader.utils import retry_utils
from chat_downloader.utils.retry_utils import RetryPolicy


def test_negative_retry_timeout_sleep_text_mentions_manual_continue() -> None:
    policy = RetryPolicy(max_attempts=3, retry_timeout=-1, interruptible_retry=True)
    assert policy.sleep_text(1) == "(press Enter to continue)"


def test_retry_policy_can_retry_and_non_interruptible_wait(monkeypatch) -> None:
    sleep = Mock()
    monkeypatch.setattr("chat_downloader.utils.retry_utils.time.sleep", sleep)

    policy = RetryPolicy(max_attempts=3, retry_timeout=2, interruptible_retry=False)

    assert policy.can_retry(2) is True
    assert policy.can_retry(3) is False
    assert policy.sleep_text(1) == "(sleep for 2.0s)"

    policy.wait(1)

    sleep.assert_called_once_with(2)


def test_retry_policy_non_numeric_timeout_returns_empty_sleep_text() -> None:
    policy = RetryPolicy(max_attempts=2, retry_timeout="later")

    assert policy.sleep_seconds(1) is None
    assert policy.sleep_text(1) == ""


def test_wait_uses_explicit_interruptible_override(monkeypatch) -> None:
    timed_input = Mock()
    sleep = Mock()
    monkeypatch.setattr("chat_downloader.utils.retry_utils.timed_input", timed_input)
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: True
    )

    policy = RetryPolicy(max_attempts=3, retry_timeout=1, interruptible_retry=False)
    policy.wait(1, interruptible=True, sleep_func=sleep)

    timed_input.assert_called_once_with(1.0)
    sleep.assert_not_called()


@pytest.mark.parametrize("pipe", [False, True])
def test_redirected_stdin_preserves_retry_delay(pipe: bool) -> None:
    script = (
        "import time; from chat_downloader.utils.retry_utils import RetryPolicy; "
        "start=time.monotonic(); RetryPolicy(retry_timeout=0.05).wait(1); "
        "print(time.monotonic()-start)"
    )
    kwargs = {"input": b""} if pipe else {"stdin": subprocess.DEVNULL}
    result = subprocess.run(  # noqa: S603 — fixed interpreter and script
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=2,
        check=True,
        **kwargs,
    )
    assert float(result.stdout) >= 0.04


def test_redirected_stdin_retry_sleeps_without_consuming_input(monkeypatch) -> None:
    reader, writer = os.pipe()
    with (
        os.fdopen(reader, encoding="utf-8") as stream,
        os.fdopen(writer, "wb", buffering=0) as output,
    ):
        output.write(b"redirected input\n")
        monkeypatch.setattr(retry_utils.sys, "stdin", stream)
        delays = []

        RetryPolicy(retry_timeout=0.05).wait(1, sleep_func=delays.append)

        assert delays == [0.05]
        assert stream.readline() == "redirected input\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal regression")
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("buffered", [False, True])
@pytest.mark.parametrize("blocking", [False, True])
def test_finite_terminal_retry_finishes_without_newline_and_restores_stdin(
    raw, buffered, blocking
) -> None:
    import tty

    script = textwrap.dedent(
        """\
        import io
        import os
        import selectors
        import sys
        import time

        from chat_downloader.utils.retry_utils import RetryPolicy
        from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT

        master, buffered, blocking = map(int, sys.argv[1:])
        raw = io.FileIO(sys.stdin.fileno(), mode="r", closefd=False)
        binary = io.BufferedReader(raw) if buffered else raw
        sys.stdin = io.TextIOWrapper(binary, encoding="utf-8")
        method = "readinto" if buffered else "read"
        setattr(raw, method, getattr(raw, method))
        original_attributes = vars(raw).copy()
        descriptor = sys.stdin.fileno()
        os.set_blocking(descriptor, bool(blocking))
        assert sys.stdin.isatty()
        assert _POLLING_STOP_EVENT.get() is None

        started = time.monotonic()
        RetryPolicy(retry_timeout=0.05).wait(1)
        elapsed = time.monotonic() - started
        assert 0.04 <= elapsed < 1
        assert os.get_blocking(descriptor) == bool(blocking)
        assert vars(raw) == original_attributes

        # No newline is supplied until the retry has finished and restored stdin.
        os.write(master, b"later\\n")
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin, selectors.EVENT_READ)
            assert selector.select(1)
        assert sys.stdin.readline() == "later\\n"
        assert os.get_blocking(descriptor) == bool(blocking)
        assert vars(raw) == original_attributes
        print(elapsed)
        """
    )
    master, slave = os.openpty()
    try:
        if raw:
            tty.setraw(slave)
        os.write(master, b"partial" if raw else b"partial\x04")
        result = subprocess.run(  # noqa: S603 — fixed interpreter and script
            [
                sys.executable,
                "-c",
                script,
                str(master),
                str(int(buffered)),
                str(int(blocking)),
            ],
            stdin=slave,
            pass_fds=(master,),
            capture_output=True,
            timeout=3,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode()
        assert float(result.stdout) >= 0.04
    finally:
        os.close(slave)
        os.close(master)


def test_manual_retry_with_closed_stdin_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: False
    )
    with pytest.raises(ChatDownloaderError, match="interactive terminal"):
        RetryPolicy(retry_timeout=-1).wait(1)


def test_manual_retry_reports_eof_after_tty_detaches(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.utils.retry_utils._stdin_is_interactive", lambda: True
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(EOFError()),
    )
    with pytest.raises(ChatDownloaderError, match="input closed"):
        RetryPolicy(retry_timeout=-1).wait(1)


def test_stdin_interactivity_handles_detached_stream(monkeypatch) -> None:
    monkeypatch.setattr(retry_utils.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    assert retry_utils._stdin_is_interactive()

    def detached() -> bool:
        raise OSError("detached")

    monkeypatch.setattr(retry_utils.sys, "stdin", SimpleNamespace(isatty=detached))
    assert not retry_utils._stdin_is_interactive()


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_sleep_text_respects_interruptible_override(attempt) -> None:
    policy = RetryPolicy(max_attempts=3, retry_timeout=1, interruptible_retry=False)
    assert (
        policy.sleep_text(attempt, interruptible=True)
        == "(sleep for 1.0s or press Enter)"
    )


def test_default_retry_wait_unwinds_when_stop_arrives_during_sleep(monkeypatch):
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()

    def cancel_during_wait(_seconds):
        stopped.set()
        return True

    monkeypatch.setattr(stopped, "wait", cancel_during_wait)
    monkeypatch.setattr(retry_utils.time, "sleep", lambda _seconds: None)
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy(retry_timeout=60, interruptible_retry=False).wait(1)
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_retry_wait_checks_stop_after_injected_sleep():
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy(retry_timeout=0, interruptible_retry=False).wait(
                1, sleep_func=lambda _seconds: stopped.set()
            )
    finally:
        _POLLING_STOP_EVENT.reset(token)


def test_untimed_manual_retry_keeps_prompt_and_enter_behavior(monkeypatch):
    prompts = []
    monkeypatch.setattr(retry_utils, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "ready"
    )
    RetryPolicy(retry_timeout=-1).wait(1)
    assert prompts == ["Press Enter to continue..."]

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


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "17"}, 17.0),
        ({"retry-after": " 42 "}, 42.0),
        ({"RETRY-AFTER": "0"}, 0.0),
        ({"Retry-After": "100000000000000000000"}, 1e20),
        ({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, 60.0),
        ({"Retry-After": "Wed, 21 Oct 2015 07:26:00 GMT"}, 0.0),
        ({"Retry-After": "Wed, 21 Oct 2015 07:27:00 GMT"}, 0.0),
        ({"Retry-After": "Wed, 21 Oct 2015 07:28:00"}, 60.0),
        (None, None),
        ([], None),
        ({}, None),
        ({"Other": "17"}, None),
        ({"Retry-After": None}, None),
        ({"Retry-After": 17}, None),
        *[
            ({"Retry-After": value}, None)
            for value in (
                "",
                "soon",
                "-1",
                "+1",
                "1.5",
                "inf",
                "NaN",
                "\u0661",
                "9" * 5000,
                "Wed, 21 Oct 10000 07:28:00 GMT",
            )
        ],
    ],
)
def test_retry_after_header_parsing(headers, expected, monkeypatch):
    now = 1445412420.0
    monkeypatch.setattr(retry_utils.time, "time", lambda: now)
    assert retry_utils.parse_retry_after(headers) == expected
    assert retry_utils.parse_retry_after(headers, now=now) == expected


@pytest.mark.parametrize(
    ("delay", "expected"),
    [
        (0, 0.0),
        (7.5, 7.5),
        (True, 3.0),
        (-1, 3.0),
        (float("inf"), 3.0),
        (float("nan"), 3.0),
        (10**1000, 3.0),
        ("8", 3.0),
        (None, 3.0),
    ],
)
def test_error_delay_recovery_validates_sanitized_hint(delay, expected):
    error = SimpleNamespace(
        retry_after=delay,
        response=SimpleNamespace(headers={"Retry-After": "3"}),
    )
    assert retry_utils.get_retry_after(error) == expected
    assert retry_utils.get_retry_after(Exception("offline")) is None


@pytest.mark.parametrize(("local", "minimum", "expected"), [(2, 5, 5), (8, 5, 8)])
def test_server_minimum_preserves_local_backoff_and_cannot_be_enter_skipped(
    monkeypatch, local, minimum, expected
):
    entered = Mock()
    sleeps = []
    monkeypatch.setattr(retry_utils, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(retry_utils, "timed_input", entered)
    RetryPolicy(retry_timeout=local).wait(
        1, server_minimum=minimum, sleep_func=sleeps.append
    )
    assert sleeps == [expected]
    entered.assert_not_called()


def test_manual_enter_waits_until_server_minimum_has_elapsed(monkeypatch):
    events = []
    monkeypatch.setattr(retry_utils, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        retry_utils,
        "timed_input",
        lambda **kwargs: events.append(("input", kwargs)),
    )
    RetryPolicy(retry_timeout=-1).wait(
        1, server_minimum=5, sleep_func=lambda delay: events.append(("sleep", delay))
    )
    assert events == [
        ("sleep", 5.0),
        ("input", {"prompt": "Press Enter to continue..."}),
    ]


def test_server_zero_still_allows_interactive_local_backoff(monkeypatch):
    entered = Mock()
    monkeypatch.setattr(retry_utils, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(retry_utils, "timed_input", entered)
    RetryPolicy(retry_timeout=2).wait(1, server_minimum=0)
    entered.assert_called_once_with(2.0)


def test_large_server_delay_is_chunked_without_shortening(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retry_utils, "_MAX_SLEEP_SECONDS", 2)
    RetryPolicy(retry_timeout=0, interruptible_retry=False).wait(
        1, server_minimum=5.5, sleep_func=sleeps.append
    )
    assert sleeps == [2, 2, 1.5]


@pytest.mark.parametrize("manual", [False, True])
def test_server_minimum_is_cancelled_before_retry_or_manual_input(monkeypatch, manual):
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    entered = Mock()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        stopped.set()

    monkeypatch.setattr(retry_utils, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(retry_utils, "_MAX_SLEEP_SECONDS", 2)
    monkeypatch.setattr(retry_utils, "timed_input", entered)
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy(retry_timeout=-1 if manual else 0).wait(
                1, server_minimum=5, sleep_func=sleep
            )
    finally:
        _POLLING_STOP_EVENT.reset(token)
    assert sleeps == [2]
    entered.assert_not_called()


def test_server_minimum_does_not_sleep_when_already_cancelled():
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    stopped.set()
    sleep = Mock()
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            RetryPolicy().wait(1, server_minimum=5, sleep_func=sleep)
    finally:
        _POLLING_STOP_EVENT.reset(token)
    sleep.assert_not_called()


def test_integer_delays_round_up_rather_than_retry_early():
    value = 2**53 + 1
    expected = float(2**53 + 2)
    assert retry_utils.parse_retry_after({"Retry-After": str(value)}) == expected
    assert retry_utils.get_retry_after(SimpleNamespace(retry_after=value)) == expected
    assert retry_utils.parse_retry_after({"Retry-After": "0" * 5000}) == 0.0


@pytest.mark.parametrize("now", [float("inf"), float("-inf"), float("nan")])
def test_http_date_with_unrepresentable_remaining_delay_is_ignored(now):
    assert (
        retry_utils.parse_retry_after(
            {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, now=now
        )
        is None
    )

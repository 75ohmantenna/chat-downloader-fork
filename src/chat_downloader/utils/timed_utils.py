# SPDX-License-Identifier: MIT

"""Timed input, interruptible sleep, and timeout-aware generator wrapper."""

from __future__ import annotations

import contextlib
import io
import queue as _queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, NoReturn, Self, TextIO, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

POLLING_TIME = 0.1

SP = " "
CR = "\r"
LF = "\n"
CRLF = CR + LF


class TimeoutOccurred(Exception):
    """Thrown when a timeout has occurred."""


def echo(text: str) -> None:
    """Write ``text`` to stdout and flush immediately."""
    sys.stdout.write(text)
    sys.stdout.flush()


# Adapted from https://github.com/johejo/inputimeout

try:
    import msvcrt

    def win_timed_input(timeout: float, prompt: str, newline: bool) -> str:
        """Read a line from the Windows console within ``timeout`` seconds.

        Args:
            timeout: Maximum seconds to wait for input.
            prompt: Prompt string shown to the user.
            newline: If True, emit a newline when the timeout expires.

        Returns:
            The line of input typed by the user.

        Raises:
            TimeoutOccurred: If the user does not press Enter within
                ``timeout``.
        """
        echo(prompt)
        begin = time.monotonic()
        end = begin + timeout
        line = ""

        while time.monotonic() < end:
            # msvcrt.kbhit/getwche are Windows-only;
            # absent from non-Windows type stubs.
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                c = msvcrt.getwche()  # type: ignore[attr-defined]
                if c in (CR, LF):
                    echo(CRLF)
                    return line
                if c == "\003":
                    raise KeyboardInterrupt
                if c == "\b":
                    line = line[:-1]
                    cover = SP * len(prompt + line + SP)
                    echo(CR + cover + CR + prompt + line)
                else:
                    line += c

            time.sleep(POLLING_TIME)

        if newline:
            echo(CRLF)

        raise TimeoutOccurred

    _timed_input = win_timed_input

except ImportError:
    import selectors
    import termios

    def posix_timed_input(timeout: float, prompt: str, newline: bool) -> str:
        """Read a line from stdin within ``timeout`` seconds on POSIX systems.

        Args:
            timeout: Maximum seconds to wait for input.
            prompt: Prompt string shown to the user.
            newline: If True, emit a newline when the timeout expires.

        Returns:
            The line of input typed by the user.

        Raises:
            TimeoutOccurred: If no input is received within ``timeout``.
        """
        echo(prompt)
        sel = selectors.DefaultSelector()
        try:
            sel.register(sys.stdin, selectors.EVENT_READ)
        except (ValueError, AttributeError, io.UnsupportedOperation):
            # Under pytest or other input-capturing environments, sys.stdin
            # may not be a real file descriptor. Treat as a timeout so
            # timed_input() returns its default.
            if newline:
                echo(LF)
            raise TimeoutOccurred from None

        events = sel.select(timeout)

        if events:
            key, _ = events[0]
            return cast("TextIO", key.fileobj).readline().rstrip(LF)
        if newline:
            echo(LF)
        # Best-effort only (stdin may not support tcflush).
        with contextlib.suppress(
            OSError,
            termios.error,
            ValueError,
            AttributeError,
            io.UnsupportedOperation,
        ):
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        raise TimeoutOccurred

    _timed_input = posix_timed_input


def timed_input(
    timeout: float | None = None,
    prompt: str = "",
    newline: bool = False,
    default: Any = None,
) -> str | Any:
    """Read a line of input with an optional timeout.

    Args:
        timeout: Seconds to wait; ``None`` means wait indefinitely.
        prompt: Prompt string displayed to the user.
        newline: If True, emit a newline after timeout expiry.
        default: Value returned when the timeout elapses without input.

    Returns:
        The user's input string, or ``default`` on timeout.
    """
    if timeout is None:
        return input(prompt)
    try:
        return _timed_input(timeout, prompt, newline)
    except TimeoutOccurred:
        return default


class TimedGenerator:
    """Add timing functionality to generator objects.

    Used to create timed-generator objects as well as add inactivity
    functionality (i.e. return if no items have been generated in a given time
    period)
    """

    def __init__(
        self,
        generator: Generator[Any, Any, Any] | Iterator[Any],
        timeout: float | None = None,
        inactivity_timeout: float | None = None,
        on_timeout: Callable[..., Any] | None = None,
        on_inactivity_timeout: Callable[..., Any] | None = None,
    ) -> None:
        """Wrap a generator with overall and inactivity timeout handling."""
        self.generator = generator
        self.timeout = timeout
        self.inactivity_timeout = inactivity_timeout

        self.on_timeout = on_timeout
        self.on_inactivity_timeout = on_inactivity_timeout

        self.timer: threading.Timer | None = None
        self.inactivity_timer: threading.Timer | None = None
        self._closed = False
        self._stop_requested = threading.Event()
        self._worker_state_lock = threading.Lock()
        self._worker_advancing = False
        self._start_time = time.monotonic()
        self._timeout_expired = threading.Event()
        self._inactivity_expired = threading.Event()
        self._timeout_deadline: float | None = (
            self._start_time + timeout if timeout is not None else None
        )
        self._inactivity_deadline: float | None = (
            self._start_time + inactivity_timeout
            if inactivity_timeout is not None
            else None
        )

        if self.timeout is not None:
            self.start_timer()

        if self.inactivity_timeout is not None:
            self.start_inactivity_timer()

        # Persistent worker — avoids spawning a new thread on every __next__()
        # call.
        self._result_queue: _queue.Queue[tuple[str, Any, float]] = _queue.Queue(
            maxsize=1
        )
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def start_timer(self) -> None:
        """Start (or restart) the overall timeout timer."""
        if self.timeout is None:
            raise RuntimeError(
                "start_timer() called without a configured timeout"
            )
        self._timeout_expired.clear()
        self._timeout_deadline = time.monotonic() + self.timeout

        def on_timeout() -> None:
            self._timeout_expired.set()

        self.timer = threading.Timer(self.timeout, on_timeout)
        self.timer.start()

    def start_inactivity_timer(self) -> None:
        """Start (or restart) the inactivity timeout timer."""
        if self.inactivity_timeout is None:
            raise RuntimeError(
                "start_inactivity_timer() called without a configured "
                "inactivity_timeout"
            )
        self._inactivity_expired.clear()
        self._inactivity_deadline = time.monotonic() + self.inactivity_timeout

        def on_inactivity_timeout() -> None:
            self._inactivity_expired.set()

        self.inactivity_timer = threading.Timer(
            self.inactivity_timeout,
            on_inactivity_timeout,
        )
        self.inactivity_timer.start()

    def reset_inactivity_timer(self) -> None:
        """Cancel the current inactivity timer and start a fresh one."""
        if self.inactivity_timer:
            self.inactivity_timer.cancel()
            self.start_inactivity_timer()

    def __iter__(self) -> Self:
        """Return this object as its own iterator."""
        return self

    def _timeout_reason(self, at_time: float | None = None) -> str | None:
        if self._timeout_expired.is_set():
            return "timeout"
        if self._inactivity_expired.is_set():
            return "inactivity"

        now = time.monotonic() if at_time is None else at_time
        if self._timeout_deadline is not None and now >= self._timeout_deadline:
            return "timeout"
        if (
            self._inactivity_deadline is not None
            and now >= self._inactivity_deadline
        ):
            return "inactivity"

        return None

    @staticmethod
    def _is_reentrant_generator_close_error(error: Exception) -> bool:
        """Return True when close() raced with an active generator iteration."""
        return (
            isinstance(error, ValueError)
            and str(error) == "generator already executing"
        )

    def _cancel_timers(self) -> None:
        self._stop_requested.set()
        if self.timer is not None:
            self.timer.cancel()
        if self.inactivity_timer is not None:
            self.inactivity_timer.cancel()
        with self._worker_state_lock:
            worker_advancing = self._worker_advancing
        if worker_advancing:
            return
        self._close_generator()

    def _close_generator(self) -> None:
        close = getattr(self.generator, "close", None)
        if callable(close):
            try:
                close()
            except (RuntimeError, StopIteration, ValueError) as error:
                if self._is_reentrant_generator_close_error(error):
                    return
                from chat_downloader.debugging import log  # cycle guard

                log("debug", f"Suppressed generator close() error: {error}")

    def _worker_loop(self) -> None:
        while not self._stop_requested.is_set():
            try:
                with self._worker_state_lock:
                    self._worker_advancing = True
                try:
                    item = next(self.generator)
                finally:
                    with self._worker_state_lock:
                        self._worker_advancing = False
                if self._stop_requested.is_set():
                    self._close_generator()
                    return
                self._result_queue.put(("item", item, time.monotonic()))
            except StopIteration:
                self._result_queue.put(("stop", None, time.monotonic()))
                return
            except BaseException as error:
                if isinstance(error, (SystemExit, GeneratorExit)):
                    raise
                self._result_queue.put(("error", error, time.monotonic()))
                return

    def _deadline_wait(self) -> float | None:
        """Return the minimum remaining deadline; None when no timers active."""
        times = []
        if self._timeout_deadline is not None:
            times.append(self._timeout_deadline - time.monotonic())
        if self._inactivity_deadline is not None:
            times.append(self._inactivity_deadline - time.monotonic())
        if not times:
            return None
        return max(0.0, min(times))

    def _finish(self, reason: str | None) -> NoReturn:
        """Cancel timers, close, run callback for reason, then stop."""
        self._cancel_timers()
        self._closed = True
        if reason == "timeout":
            self._run_function(self.on_timeout)
        elif reason is not None:
            self._run_function(self.on_inactivity_timeout)
        raise StopIteration

    def _handle_error_result(
        self, captured_error: BaseException, completed_at: float
    ) -> NoReturn:
        """Raise or finish based on the error kind and deadline state."""
        reason = self._timeout_reason(completed_at)
        if isinstance(captured_error, StopIteration):
            self._cancel_timers()
            self._closed = True
            raise captured_error
        if isinstance(captured_error, KeyboardInterrupt):
            if reason is None:
                self._cancel_timers()
                self._closed = True
                raise captured_error
            self._finish(reason)
        raise captured_error

    def _handle_item_result(self, value: Any, completed_at: float) -> Any:
        """Apply post-item deadline check, reset inactivity, return item."""
        reason = self._timeout_reason(completed_at)
        if reason is not None:
            self._finish(reason)
        self.reset_inactivity_timer()
        return value

    def __next__(self) -> Any:
        """Return the next item or stop when configured timers expire."""
        if self._closed:
            raise StopIteration

        try:
            kind, value, completed_at = self._result_queue.get(
                timeout=self._deadline_wait()
            )
        except _queue.Empty:
            reason = self._timeout_reason()
            if reason is None:
                reason = "timeout" if self.timer is not None else "inactivity"
            self._finish(reason)

        if kind == "error":
            self._handle_error_result(value, completed_at)

        if kind == "stop":
            self._finish(None)

        return self._handle_item_result(value, completed_at)

    def _run_function(self, function: Callable[[], Any] | None) -> None:
        if callable(function):
            function()


def polling_sleep(secs: float, poll_time: float = POLLING_TIME) -> None:
    """Sleep for ``secs`` seconds using short polling intervals.

    Args:
        secs: Total duration to sleep in seconds.
        poll_time: Length of each polling interval in seconds.
    """
    if secs <= 0:
        return

    start_time = time.time()

    while time.time() - start_time < secs:
        time.sleep(poll_time)

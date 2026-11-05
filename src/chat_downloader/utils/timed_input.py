# SPDX-License-Identifier: MIT

"""Console input with an optional per-read timeout."""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, TextIO, cast

from .timed_generator import (
    _POLLING_STOP_EVENT,
    _check_polling_cancelled,
    polling_sleep,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

SP = " "
CR = "\r"
LF = "\n"
CRLF = CR + LF

# Maximum wait between cancellation checks while reading console input.
_INPUT_POLL_SECONDS = 0.1
_INPUT_LOCK = threading.RLock()


@contextlib.contextmanager
def _input_owner(timeout: float | None) -> Iterator[float | None]:
    """Own stdin across adaptation, reading and cleanup, with bounded acquisition."""
    _check_polling_cancelled()
    started = None
    if not _INPUT_LOCK.acquire(blocking=False):
        started = time.monotonic()
        while True:
            _check_polling_cancelled()
            remaining = (
                None
                if timeout is None
                else max(0.0, timeout - (time.monotonic() - started))
            )
            interval = (
                _INPUT_POLL_SECONDS
                if remaining is None
                else min(_INPUT_POLL_SECONDS, remaining)
            )
            if _INPUT_LOCK.acquire(timeout=interval):
                break
            if remaining is not None and remaining <= interval:
                raise TimeoutOccurred
    try:
        _check_polling_cancelled()
        remaining = timeout
        if started is not None and timeout is not None:
            remaining = max(0.0, timeout - (time.monotonic() - started))
            if remaining <= 0:
                raise TimeoutOccurred
        yield remaining
    finally:
        _INPUT_LOCK.release()


class TimeoutOccurred(Exception):
    """Thrown when a timeout has occurred."""


def echo(text: str) -> None:
    """Write ``text`` to stdout and flush immediately."""
    sys.stdout.write(text)
    sys.stdout.flush()


# Adapted from https://github.com/johejo/inputimeout

try:
    import msvcrt

    def win_timed_input(timeout: float | None, prompt: str, *, newline: bool) -> str:
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
        _check_polling_cancelled()
        echo(prompt)
        end = None if timeout is None else time.monotonic() + timeout
        line = ""

        while end is None or time.monotonic() < end:
            _check_polling_cancelled()
            # msvcrt.kbhit/getwche are Windows-only;
            # absent from non-Windows type stubs.
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                _check_polling_cancelled()
                c = msvcrt.getwche()  # type: ignore[attr-defined]
                _check_polling_cancelled()
                if c in (CR, LF):
                    echo(CRLF)
                    return line
                if c == "\003":
                    raise KeyboardInterrupt
                if c == "\032" and timeout is None and not line:
                    raise EOFError
                if c == "\b":
                    line = line[:-1]
                    cover = SP * len(prompt + line + SP)
                    echo(CR + cover + CR + prompt + line)
                else:
                    line += c

            if _POLLING_STOP_EVENT.get() is None:
                time.sleep(_INPUT_POLL_SECONDS)
            else:
                polling_sleep(_INPUT_POLL_SECONDS)

        _check_polling_cancelled()
        if newline:
            echo(CRLF)

        raise TimeoutOccurred

    _timed_input = win_timed_input

except ImportError:
    import os
    import selectors
    import termios

    def _read_posix_cancellable(  # noqa: C901 — one decoded input lifetime
        sel: selectors.BaseSelector, timeout: float | None
    ) -> str:
        """Read through stdin's decoder without blocking on an unfinished line."""
        descriptor = sys.stdin.fileno()
        was_blocking = os.get_blocking(descriptor)
        started = time.monotonic()
        remaining = timeout
        line: list[str] = []
        buffer = sys.stdin.buffer
        raw = getattr(buffer, "raw", buffer)
        method_name = "readinto" if raw is not buffer else "read"
        original_read = cast("Callable[[object], object]", getattr(raw, method_name))
        had_override = method_name in vars(raw)
        previous_override = vars(raw).get(method_name)

        def read_available(argument: object) -> object:
            result = original_read(argument)
            if result is None:
                # Do not let temporary exhaustion finalize stdin's decoder.
                raise BlockingIOError
            return result

        setattr(raw, method_name, read_available)
        try:
            os.set_blocking(descriptor, False)
            while True:
                _check_polling_cancelled()
                try:
                    character = sys.stdin.read(1)
                except BlockingIOError:
                    character = None
                _check_polling_cancelled()
                if character == LF:
                    return "".join(line)
                if character:
                    line.append(character)
                elif character is not None:
                    if line:
                        return "".join(line)
                    if timeout is None:
                        raise EOFError
                    polling_sleep(max(0.0, timeout - (time.monotonic() - started)))
                    raise TimeoutOccurred
                else:
                    interval = (
                        _INPUT_POLL_SECONDS
                        if remaining is None
                        else min(_INPUT_POLL_SECONDS, max(0.0, remaining))
                    )
                    sel.select(interval)
                    _check_polling_cancelled()
                if timeout is not None and (timeout > 0 or not character):
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutOccurred
        finally:
            try:
                os.set_blocking(descriptor, was_blocking)
            finally:
                if had_override:
                    setattr(raw, method_name, previous_override)
                else:
                    delattr(raw, method_name)

    def posix_timed_input(timeout: float | None, prompt: str, *, newline: bool) -> str:
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
        with _input_owner(timeout) as timeout:
            _check_polling_cancelled()
            echo(prompt)
            sel = selectors.DefaultSelector()
            try:
                try:
                    sel.register(sys.stdin, selectors.EVENT_READ)
                except (OSError, ValueError, AttributeError, io.UnsupportedOperation):
                    # Captured or detached stdin may have no selectable descriptor.
                    _check_polling_cancelled()
                    if timeout is None:
                        raise EOFError from None
                    if newline:
                        echo(LF)
                    raise TimeoutOccurred from None

                try:
                    if _POLLING_STOP_EVENT.get() is not None:
                        return _read_posix_cancellable(sel, timeout)
                    started = time.monotonic()
                    events = sel.select(timeout)
                    if events:
                        key, _ = events[0]
                        line = cast("TextIO", key.fileobj).readline()
                        if line:
                            return line.rstrip(LF)
                        if timeout is None:
                            raise EOFError
                        time.sleep(max(0.0, timeout - (time.monotonic() - started)))
                except TimeoutOccurred:
                    pass

                _check_polling_cancelled()
                if newline:
                    echo(LF)
                # Flush only once at the actual input timeout, never per short poll.
                _check_polling_cancelled()
                with contextlib.suppress(
                    OSError,
                    termios.error,
                    ValueError,
                    AttributeError,
                    io.UnsupportedOperation,
                ):
                    termios.tcflush(sys.stdin, termios.TCIFLUSH)
                raise TimeoutOccurred
            finally:
                sel.close()

    _timed_input = posix_timed_input


def timed_input(
    timeout: float | None = None,
    prompt: str = "",
    *,
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
    _check_polling_cancelled()
    try:
        with _input_owner(timeout) as remaining:
            if timeout is None and _POLLING_STOP_EVENT.get() is None:
                return input(prompt)
            value = _timed_input(remaining, prompt, newline=newline)
    except TimeoutOccurred:
        value = default
    _check_polling_cancelled()
    return value

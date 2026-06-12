# SPDX-License-Identifier: MIT

"""Console input with an optional per-read timeout."""

from __future__ import annotations

import contextlib
import io
import sys
import time
from typing import Any, TextIO, cast

SP = " "
CR = "\r"
LF = "\n"
CRLF = CR + LF

# Local polling interval — avoids importing timed_generator.
_INPUT_POLL_SECONDS = 0.1


class TimeoutOccurred(Exception):
    """Thrown when a timeout has occurred."""


def echo(text: str) -> None:
    """Write ``text`` to stdout and flush immediately."""
    sys.stdout.write(text)
    sys.stdout.flush()


# Adapted from https://github.com/johejo/inputimeout

try:
    import msvcrt

    def win_timed_input(timeout: float, prompt: str, *, newline: bool) -> str:
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

            time.sleep(_INPUT_POLL_SECONDS)

        if newline:
            echo(CRLF)

        raise TimeoutOccurred

    _timed_input = win_timed_input

except ImportError:
    import selectors
    import termios

    def posix_timed_input(timeout: float, prompt: str, *, newline: bool) -> str:
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
    if timeout is None:
        return input(prompt)
    try:
        return _timed_input(timeout, prompt, newline=newline)
    except TimeoutOccurred:
        return default

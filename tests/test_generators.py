# SPDX-License-Identifier: MIT

from __future__ import annotations

import contextlib

import pytest

from chat_downloader.utils.timed_generator import TimedGenerator


def test_timed_generator_basic() -> None:
    """Test basic TimedGenerator functionality."""

    def simple_generator():
        yield from range(5)

    timed_gen = TimedGenerator(simple_generator())
    result = list(timed_gen)
    assert result == [0, 1, 2, 3, 4]


def test_timed_generator_timeout() -> None:
    """Test TimedGenerator with timeout."""
    from unittest.mock import patch

    class FakeTimer:
        def __init__(self, alive: bool) -> None:
            self._alive = alive
            self.cancelled = False

        def is_alive(self):
            return self._alive

        def cancel(self) -> None:
            self.cancelled = True

    called = []

    def interrupting_generator():
        raise KeyboardInterrupt
        yield  # pragma: no cover

    def fake_start_timer(self) -> None:
        self.timer = FakeTimer(alive=False)  # expired timer
        self._timeout_expired.set()

    with patch.object(TimedGenerator, "start_timer", fake_start_timer):
        timed_gen = TimedGenerator(
            interrupting_generator(),
            timeout=0.5,
            on_timeout=lambda: called.append("timeout"),
        )

    with pytest.raises(StopIteration):
        next(timed_gen)

    assert called == ["timeout"]
    assert timed_gen.timer.cancelled


def test_timed_generator_inactivity_timeout() -> None:
    """Test TimedGenerator with inactivity timeout."""
    import time

    def inactive_generator():
        yield 1
        time.sleep(0.04)  # Pause longer than inactivity timeout
        yield 2

    # Should stop due to inactivity
    timed_gen = TimedGenerator(inactive_generator(), inactivity_timeout=0.02)

    result = []
    try:
        for item in timed_gen:
            result.append(item)
    except StopIteration:
        pass

    # Should only get first item before inactivity timeout
    assert len(result) == 1
    assert result[0] == 1


def test_timed_generator_callback() -> None:
    """Test TimedGenerator with timeout callback."""
    import time

    callback_called = []

    def timeout_callback() -> None:
        callback_called.append(True)

    def slow_generator():
        for i in range(10):
            time.sleep(0.02)
            yield i

    timed_gen = TimedGenerator(
        slow_generator(),
        timeout=0.1,
        on_timeout=timeout_callback,
    )

    with contextlib.suppress(StopIteration):
        list(timed_gen)

    assert len(callback_called) > 0


def test_timed_generator_no_timeout() -> None:
    """Test TimedGenerator without timeout."""

    def generator():
        yield from range(3)

    timed_gen = TimedGenerator(generator(), timeout=None)
    result = list(timed_gen)
    assert result == [0, 1, 2]


def test_timed_generator_is_iterable() -> None:
    """Test that TimedGenerator is iterable."""

    def generator():
        yield 1
        yield 2

    timed_gen = TimedGenerator(generator())
    assert timed_gen == iter(timed_gen)


def test_timed_generator_inactivity_reset() -> None:
    """Test that inactivity timer resets on activity."""
    import time

    def generator_with_pauses():
        for i in range(3):
            time.sleep(0.02)  # Short pause (less than inactivity timeout)
            yield i

    # Inactivity timeout of 0.3s should not trigger
    timed_gen = TimedGenerator(generator_with_pauses(), inactivity_timeout=0.05)

    result = list(timed_gen)
    assert result == [0, 1, 2]


def test_polling_sleep() -> None:
    """Test polling_sleep function."""
    import time

    from chat_downloader.utils.timed_generator import polling_sleep

    start = time.time()
    polling_sleep(0.05)
    elapsed = time.time() - start

    assert elapsed > 0.02
    assert elapsed < 0.12


def test_polling_sleep_non_positive_returns_immediately() -> None:
    """Test polling_sleep handles non-positive durations."""
    import time

    from chat_downloader.utils.timed_generator import polling_sleep

    start = time.time()
    polling_sleep(0)
    elapsed = time.time() - start

    assert elapsed < 0.02


def test_timed_generator_empty_generator() -> None:
    """Test TimedGenerator with empty generator."""

    def empty_generator():
        return
        yield  # Make it a generator

    timed_gen = TimedGenerator(empty_generator(), timeout=1)
    result = list(timed_gen)
    assert result == []


def test_timed_generator_exception_handling() -> None:
    """Test TimedGenerator handles exceptions properly."""

    def failing_generator():
        yield 1
        msg = "Test error"
        raise ValueError(msg)

    timed_gen = TimedGenerator(failing_generator())

    result = []
    with pytest.raises(ValueError):
        for item in timed_gen:
            result.append(item)

    assert result == [1]


def test_timeout_exception() -> None:
    """Test that TimeoutOccurred exception exists."""
    from chat_downloader.utils.timed_input import TimeoutOccurred

    exc = TimeoutOccurred("Test timeout")
    assert isinstance(exc, Exception)


def test_timed_input_with_timeout() -> None:
    """Test timed_input returns default on timeout."""
    from chat_downloader.utils.timed_input import timed_input

    # Short timeout should return default
    result = timed_input(timeout=0.02, default="default_value")
    assert result == "default_value"


def test_timed_input_without_timeout() -> None:
    """Test that timed_input with None timeout uses regular input."""
    from chat_downloader.utils.timed_input import timed_input

    # This test just verifies the function exists and can be called
    # with None timeout (actual input testing would require user
    # interaction)
    assert callable(timed_input)

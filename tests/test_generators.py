# SPDX-License-Identifier: MIT

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from chat_downloader.utils import timed_generator as generator_module
from chat_downloader.utils.timed_generator import TimedGenerator


def test_timed_generator_basic() -> None:
    """Test basic TimedGenerator functionality."""

    def simple_generator():
        yield from range(5)

    timed_gen = TimedGenerator(simple_generator())
    result = list(timed_gen)
    assert result == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("option", ["timeout", "inactivity_timeout"])
def test_real_deadline_stops_blocked_source_and_calls_callback_once(option):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    callbacks = []

    def source():
        try:
            entered.set()
            assert release.wait(5), "test did not release the blocked source"
            yield "after shutdown"
        finally:
            closed.set()

    timed_gen = TimedGenerator(
        source(), **{option: 0.05, f"on_{option}": lambda: callbacks.append(option)}
    )
    try:
        assert entered.wait(5)
        assert list(timed_gen) == []
        assert list(timed_gen) == []
        assert callbacks == [option]
    finally:
        release.set()
        timed_gen.close()
        timed_gen._worker.join(5)
    assert not timed_gen._worker.is_alive()
    assert closed.is_set()


@pytest.mark.parametrize(
    ("seconds", "expected_sleeps"),
    [(0, []), (-1, []), (0.05, [0.1]), (0.25, [0.1, 0.1, 0.1])],
)
def test_polling_sleep_uses_requested_duration_and_poll_interval(
    monkeypatch, seconds, expected_sleeps
):
    elapsed = 0.0
    sleeps = []

    def sleep(duration):
        nonlocal elapsed
        sleeps.append(duration)
        elapsed += duration

    monkeypatch.setattr(
        generator_module,
        "time",
        SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep),
    )
    generator_module.polling_sleep(seconds, poll_time=0.1)
    assert sleeps == expected_sleeps


def test_timed_generator_is_iterable() -> None:
    """Test that TimedGenerator is iterable."""

    def generator():
        yield 1
        yield 2

    timed_gen = TimedGenerator(generator())
    try:
        assert timed_gen is iter(timed_gen)
    finally:
        timed_gen.close()


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
        result.extend(timed_gen)

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

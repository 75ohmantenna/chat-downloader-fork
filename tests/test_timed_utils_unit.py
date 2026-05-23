# SPDX-License-Identifier: MIT

import queue
import time
from unittest.mock import patch

import pytest

from chat_downloader.utils.timed_utils import TimedGenerator


class _FakeTimer:
    def __init__(self, alive: bool) -> None:
        self._alive = alive
        self.cancelled = False

    def is_alive(self):
        return self._alive

    def cancel(self) -> None:
        self.cancelled = True


def test_timed_generator_keyboard_interrupt_no_timers_propagates() -> None:
    def gen():
        raise KeyboardInterrupt
        yield  # pragma: no cover

    tg = TimedGenerator(gen())
    with pytest.raises(KeyboardInterrupt):
        next(tg)


def test_timed_generator_timeout_path_raises_stop_iteration_and_calls_callback() -> (
    None
):
    called = []

    def gen():
        raise KeyboardInterrupt
        yield  # pragma: no cover

    def fake_start_timer(self) -> None:
        self.timer = _FakeTimer(alive=False)
        self._timeout_expired.set()

    with patch.object(TimedGenerator, "start_timer", fake_start_timer):
        tg = TimedGenerator(
            gen(),
            timeout=1,
            on_timeout=lambda: called.append("timeout"),
        )

    with pytest.raises(StopIteration):
        next(tg)

    assert called == ["timeout"]
    assert tg.timer.cancelled is True


def test_timed_generator_ignores_stale_timeout_thread_when_item_arrives_in_time() -> (
    None
):
    def gen():
        yield 1

    def fake_start_timer(self) -> None:
        # Simulate a timeout thread that has already stopped (is_alive=False)
        # but whose deadline has not been reached. A previous implementation
        # treated any non-alive timer as an immediate timeout and dropped items.
        self.timer = _FakeTimer(alive=False)
        self._timeout_deadline = time.monotonic() + 60

    called = []
    with patch.object(TimedGenerator, "start_timer", fake_start_timer):
        tg = TimedGenerator(
            gen(),
            timeout=10,
            on_timeout=lambda: called.append("timeout"),
        )

    assert next(tg) == 1
    assert called == []


def test_timed_generator_ignores_stale_inactivity_thread_when_item_arrives_in_time() -> (
    None
):
    def gen():
        yield 1

    def fake_start_inactivity_timer(self) -> None:
        # Simulate a timer thread that is not alive (asynchronous cleanup race)
        # while inactivity deadline is still in the future.
        self.inactivity_timer = _FakeTimer(alive=False)
        self._inactivity_deadline = time.monotonic() + 60

    called = []
    with patch.object(
        TimedGenerator, "start_inactivity_timer", fake_start_inactivity_timer
    ):
        tg = TimedGenerator(
            gen(),
            inactivity_timeout=10,
            on_inactivity_timeout=lambda: called.append("inactivity"),
        )

    assert next(tg) == 1
    assert called == []


def test_timed_generator_inactivity_timeout_path_raises_stop_iteration_and_calls_callback() -> (
    None
):
    called = []

    def gen():
        raise KeyboardInterrupt
        yield  # pragma: no cover

    def fake_start_inactivity_timer(self) -> None:
        self.inactivity_timer = _FakeTimer(alive=False)
        self._inactivity_expired.set()

    with patch.object(
        TimedGenerator, "start_inactivity_timer", fake_start_inactivity_timer
    ):
        tg = TimedGenerator(
            gen(),
            inactivity_timeout=1,
            on_inactivity_timeout=lambda: called.append("inact"),
        )

    with pytest.raises(StopIteration):
        next(tg)

    assert called == ["inact"]
    assert tg.inactivity_timer.cancelled is True


def test_timed_generator_basic_iteration_resets_inactivity_timer() -> None:
    def gen():
        yield 1
        yield 2

    tg = TimedGenerator(gen())
    tg.inactivity_timeout = 1
    fake = _FakeTimer(alive=True)
    tg.inactivity_timer = fake
    tg.start_inactivity_timer = lambda: None  # type: ignore[method-assign]

    assert next(tg) == 1
    # reset_inactivity_timer cancels and restarts via start_inactivity_timer,
    # but we haven't patched it here; at minimum, cancel() should have happened.
    assert fake.cancelled is True


class _FakeQueue:
    def __init__(self, *, exc=None, value=None) -> None:
        self.exc = exc
        self.value = value

    def get(self, timeout=None):
        if self.exc is not None:
            raise self.exc
        return self.value


def test_timer_callbacks_set_expiry_flags() -> None:
    tg = TimedGenerator(iter(()))
    tg.timeout = 1
    tg.inactivity_timeout = 1
    tg.start_timer()
    tg.start_inactivity_timer()
    assert tg.timer is not None and tg.inactivity_timer is not None
    tg.timer.function()
    tg.inactivity_timer.function()
    assert tg._timeout_expired.is_set()
    assert tg._inactivity_expired.is_set()
    tg._cancel_timers()


def test_next_stops_when_closed() -> None:
    tg = TimedGenerator(iter(()))
    tg._closed = True
    with pytest.raises(StopIteration):
        next(tg)
    tg._cancel_timers()


def test_next_empty_queue_falls_back_to_timeout_when_no_reason(
    monkeypatch,
) -> None:
    called = []
    tg = TimedGenerator(
        iter(()), timeout=1, on_timeout=lambda: called.append("timeout")
    )
    tg._result_queue = _FakeQueue(exc=queue.Empty())
    # type: ignore[method-assign]
    tg._timeout_reason = lambda at_time=None: None

    with pytest.raises(StopIteration):
        next(tg)

    assert called == ["timeout"]
    assert tg._closed is True


def test_next_error_stop_iteration_closes_generator() -> None:
    tg = TimedGenerator(iter(()))
    tg._result_queue = _FakeQueue(value=("error", StopIteration(), 0.0))

    with pytest.raises(StopIteration):
        next(tg)

    assert tg._closed is True


def test_next_item_after_expiry_uses_inactivity_callback() -> None:
    called = []
    tg = TimedGenerator(
        iter(()),
        inactivity_timeout=1,
        on_inactivity_timeout=lambda: called.append("inactivity"),
    )
    tg._result_queue = _FakeQueue(value=("item", "late-item", 0.0))
    # type: ignore[method-assign]
    tg._timeout_reason = lambda at_time=None: "inactivity"

    with pytest.raises(StopIteration):
        next(tg)

    assert called == ["inactivity"]
    assert tg._closed is True


def test_next_item_after_expiry_uses_timeout_callback() -> None:
    called = []
    tg = TimedGenerator(
        iter(()), timeout=1, on_timeout=lambda: called.append("timeout")
    )
    tg._result_queue = _FakeQueue(value=("item", "late-item", 0.0))
    # type: ignore[method-assign]
    tg._timeout_reason = lambda at_time=None: "timeout"

    with pytest.raises(StopIteration):
        next(tg)

    assert called == ["timeout"]
    assert tg._closed is True


def test_worker_loop_reraises_system_exit() -> None:
    class ExitIter:
        def __iter__(self):
            return self

        def __next__(self):
            raise SystemExit

    tg = TimedGenerator(iter(()))
    tg.generator = ExitIter()
    tg._result_queue = queue.Queue(maxsize=1)

    with pytest.raises(SystemExit):
        tg._worker_loop()

# SPDX-License-Identifier: MIT

from __future__ import annotations

import queue
import threading
import time
from contextlib import nullcontext
from unittest.mock import patch

import pytest

import chat_downloader.debugging as _debugging
from chat_downloader.utils.timed_generator import TimedGenerator


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


def test_timed_generator_propagates_testing_context_to_worker() -> None:
    original_mode = _debugging.get_testing_mode()

    def gen():
        _debugging.debug_log("unexpected worker data")
        yield "continued"

    try:
        _debugging.set_testing_mode(_debugging.TestingModes.EXIT_ON_DEBUG)
        tg = TimedGenerator(gen())

        with pytest.raises(_debugging.TestingException):
            next(tg)
    finally:
        _debugging.set_testing_mode(original_mode)


@pytest.fixture(params=["timeout", "inactivity"])
def timer_kind(request):
    kind = request.param
    option = "timeout" if kind == "timeout" else "inactivity_timeout"
    method = "start_timer" if kind == "timeout" else "start_inactivity_timer"
    attribute = "timer" if kind == "timeout" else "inactivity_timer"
    return kind, option, method, attribute


@pytest.mark.parametrize("expired", [False, True])
def test_stopped_timer_uses_deadline_not_thread_liveness(timer_kind, expired):
    kind, option, method, attribute = timer_kind
    called = []

    def source():
        if expired:
            raise KeyboardInterrupt
        yield 1

    def start(self):
        setattr(self, attribute, _FakeTimer(alive=False))
        setattr(self, f"_{kind}_deadline", 0.0 if expired else time.monotonic() + 60)
        if expired:
            getattr(self, f"_{kind}_expired").set()

    with patch.object(TimedGenerator, method, start):
        tg = TimedGenerator(
            source(),
            **{
                option: 1 if expired else 10,
                f"on_{option}": lambda: called.append(kind),
            },
        )
    if expired:
        with pytest.raises(StopIteration):
            next(tg)
        assert getattr(tg, attribute).cancelled is True
    else:
        assert next(tg) == 1
    assert called == ([kind] if expired else [])


def test_timed_generator_delivers_item_generated_before_inactivity_deadline() -> None:
    generated = threading.Event()
    called = []

    def gen():
        generated.set()
        yield "queued-before-deadline"

    tg = TimedGenerator(
        gen(),
        inactivity_timeout=0.1,
        on_inactivity_timeout=lambda: called.append("inactivity"),
    )
    assert generated.wait(timeout=1.0)
    time.sleep(0.15)

    assert next(tg) == "queued-before-deadline"
    assert called == []
    tg.close()


def test_timeout_reason_uses_worker_completion_time_over_expiry_events() -> None:
    tg = TimedGenerator(iter(()), timeout=10, inactivity_timeout=10)
    tg._timeout_deadline = 20.0
    tg._inactivity_deadline = 20.0
    tg._timeout_expired.set()
    tg._inactivity_expired.set()

    assert tg._timeout_reason() == "timeout"
    tg._timeout_expired.clear()
    assert tg._timeout_reason() == "inactivity"
    tg._timeout_expired.set()
    assert tg._timeout_reason(19.0) is None
    assert tg._timeout_reason(20.0) == "timeout"
    tg.close()


def test_timed_generator_basic_iteration_resets_inactivity_timer() -> None:
    tg = TimedGenerator(iter((1, 2)))
    tg.inactivity_timeout = 1
    fake = _FakeTimer(alive=True)
    tg.inactivity_timer = fake
    tg.start_inactivity_timer = lambda: None  # type: ignore[method-assign]

    assert next(tg) == 1
    assert fake.cancelled is True


class _FakeQueue:
    def __init__(self, *, exc=None, value=None) -> None:
        self.exc = exc
        self.value = value

    def get(self, timeout=None):
        if self.exc is not None:
            raise self.exc
        return self.value


def test_timer_callbacks_set_expiry_flags(timer_kind) -> None:
    kind, option, method, attribute = timer_kind
    tg = TimedGenerator(iter(()))
    setattr(tg, option, 1)
    getattr(tg, method)()
    timer = getattr(tg, attribute)
    assert timer is not None
    timer.function()
    assert getattr(tg, f"_{kind}_expired").is_set()
    tg._cancel_timers()


@pytest.mark.parametrize("close", [False, True])
def test_closed_generator_stops_idempotently(close):
    tg = TimedGenerator(iter(()))
    if close:
        tg.close()
        tg.close()
    else:
        tg._closed = True
    assert tg._closed is True
    with pytest.raises(StopIteration):
        next(tg)
    tg._cancel_timers()


@pytest.mark.parametrize(
    ("case", "kind"),
    [
        ("empty", "timeout"),
        ("stop", "timeout"),
        ("deadline", "timeout"),
        ("late", "timeout"),
        ("late", "inactivity"),
    ],
)
def test_queue_termination_closes_and_uses_matching_callback(case, kind):
    option = "timeout" if kind == "timeout" else "inactivity_timeout"
    called = []

    def start(self):
        self.timer = _FakeTimer(alive=True)
        self._timeout_deadline = 10.0

    timer_patch = patch.object(TimedGenerator, "start_timer", start)
    with timer_patch if case == "deadline" else nullcontext():
        options = {} if case == "stop" else {option: 1}
        tg = TimedGenerator(
            iter(()), **options, **{f"on_{option}": lambda: called.append(kind)}
        )
    tg._result_queue = _FakeQueue(
        exc=queue.Empty() if case == "empty" else None,
        value=("error", StopIteration(), 0.0)
        if case == "stop"
        else ("item", "late-item", 2.0 if case == "deadline" else 0.0),
    )
    if case == "deadline":
        tg._timeout_expired.clear()
        tg._timeout_deadline = 1.0
    elif case in ("late", "empty"):
        tg._timeout_reason = (  # type: ignore[method-assign]
            lambda at_time=None: kind if case == "late" else None
        )
    if case == "late":
        tg._record_prefetched_item()
    with pytest.raises(StopIteration):
        next(tg)
    assert called == ([] if case == "stop" else [kind])
    assert tg._closed is True
    if case == "late":
        assert tg.deadline_prefetch_summary() == (1, True)
    if case == "deadline":
        assert tg.timer.cancelled is True


def test_worker_counts_item_returned_while_deadline_shutdown_completes() -> None:
    advance_started = threading.Event()
    allow_item = threading.Event()

    def blocked_source():
        advance_started.set()
        allow_item.wait()
        yield "prefetched"

    tg = TimedGenerator(blocked_source(), timeout=0.01)
    assert advance_started.wait(timeout=1)

    with pytest.raises(StopIteration):
        next(tg)

    allow_item.set()
    tg._worker.join(timeout=1)

    assert tg.deadline_prefetch_summary() == (1, True)


@pytest.mark.parametrize("items", [("first", "second"), ("late",)])
def test_deadline_prefetch_and_consumer_terminal_decision(items):
    allow_finish = threading.Event()
    handling_item = threading.Event()
    tg = TimedGenerator(iter(items))
    tg._timeout_deadline = 0.0
    original_handle = tg._handle_item_result

    def delayed_handle(value, completed_at):
        handling_item.set()
        assert allow_finish.wait(timeout=1)
        return original_handle(value, completed_at)

    # Keep the consumer between dequeue and terminal decision while the worker
    # either publishes its second prefetch or finishes the single-item source.
    tg._handle_item_result = delayed_handle  # type: ignore[method-assign]
    consumer = threading.Thread(target=lambda: next(tg, None))
    consumer.start()
    assert handling_item.wait(timeout=1)
    if len(items) == 2:
        deadline = time.monotonic() + 1
        while tg._result_queue.qsize() != 1 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert tg._result_queue.qsize() == 1
    else:
        tg._worker.join(timeout=1)
        assert tg.deadline_prefetch_summary() == (0, False)
    allow_finish.set()
    consumer.join(timeout=1)
    tg._worker.join(timeout=1)
    assert tg.deadline_prefetch_summary() == (len(items), True)


def test_start_timer_requires_configured_timeout(timer_kind):
    _, _, method, _ = timer_kind
    tg = TimedGenerator(iter(()))
    with pytest.raises(RuntimeError, match=rf"{method}\(\) called without"):
        getattr(tg, method)()


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

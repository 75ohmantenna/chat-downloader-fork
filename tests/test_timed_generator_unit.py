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


class _ObservedTimerLock:
    def __init__(self, observed_thread: str) -> None:
        self._lock = threading.RLock()
        self._observed_thread = observed_thread
        self.attempted = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == self._observed_thread:
            self.attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self._lock.release()


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


def test_timed_generator_delivers_item_generated_before_deadline(
    monkeypatch, timer_kind
) -> None:
    kind, option, _, _ = timer_kind
    published = threading.Event()
    called = []
    clock = [100.0]
    original_publish = TimedGenerator._publish_result

    def publish(self, result):
        accepted = original_publish(self, result)
        if result[0] == "item":
            published.set()
        return accepted

    monkeypatch.setattr(TimedGenerator, "_publish_result", publish)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(
            iter(("queued-before-deadline",)),
            **{option: 0.1, f"on_{option}": lambda: called.append(kind)},
        )
        try:
            assert published.wait(timeout=5)
            clock[0] += 0.15
            timer.call_args.args[1]()
            assert getattr(tg, f"_{kind}_expired").is_set()

            assert next(tg) == "queued-before-deadline"
            assert called == []
        finally:
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
    before = time.monotonic()
    assert next(tg) == 1
    assert fake.cancelled is False
    assert tg.inactivity_timer is fake
    assert tg._inactivity_deadline >= before + 1
    tg.close()


def test_inactivity_timer_does_not_spawn_per_delivered_message() -> None:
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter((1, 2, 3)), inactivity_timeout=60)
        assert list(tg) == [1, 2, 3]
        assert timer.call_count == 1


def test_inactivity_timer_reschedules_at_updated_deadline() -> None:
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), inactivity_timeout=60)
        first_callback = timer.call_args.args[1]
        tg.reset_inactivity_timer()
        first_callback()
        assert timer.call_count == 2
        tg._inactivity_deadline = time.monotonic() - 1
        timer.call_args.args[1]()
        assert tg._inactivity_expired.is_set()
        tg.close()


def test_restarting_timer_cancels_previous_timer(timer_kind) -> None:
    _, option, method, _ = timer_kind
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), **{option: 60})
        getattr(tg, method)()
        timer.return_value.cancel.assert_called_once()
        tg.close()


def test_restarted_real_timers_stop_after_close(timer_kind) -> None:
    _, option, method, attribute = timer_kind
    tg = TimedGenerator(iter(()), **{option: 60})
    timers = [getattr(tg, attribute)]
    try:
        getattr(tg, method)()
        timers.append(getattr(tg, attribute))
        assert timers[0] is not timers[1]
        tg.close()
        for timer in timers:
            timer.join(timeout=1)
            assert not timer.is_alive()
    finally:
        tg.close()
        for timer in timers:
            timer.cancel()
            timer.join(timeout=1)


@pytest.mark.parametrize("callback_time", [165.0, 175.0])
def test_cancelled_timer_callback_cannot_affect_restarted_deadline(
    timer_kind, monkeypatch, callback_time
) -> None:
    kind, option, method, attribute = timer_kind
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), **{option: 60})
        try:
            old_callback = timer.call_args.args[1]
            clock[0] = 110.0
            getattr(tg, method)()
            current_timer = getattr(tg, attribute)
            current_callback = timer.call_args.args[1]

            clock[0] = callback_time
            old_callback()

            assert timer.call_count == 2
            assert getattr(tg, attribute) is current_timer
            assert getattr(tg, f"_{kind}_deadline") == 170.0
            assert not getattr(tg, f"_{kind}_expired").is_set()
            assert tg._timeout_reason(169.0) is None
            assert tg._timeout_reason(170.0) == kind

            clock[0] = 170.0
            current_callback()
            assert getattr(tg, f"_{kind}_expired").is_set()
        finally:
            tg.close()


@pytest.mark.parametrize("shutdown", ["close", "_cancel_timers"])
def test_stopped_timer_cannot_restart_or_reset_expiry(timer_kind, shutdown) -> None:
    kind, option, method, attribute = timer_kind
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), **{option: 60})
        try:
            current_timer = getattr(tg, attribute)
            expired = getattr(tg, f"_{kind}_expired")
            expired.set()
            deadline = getattr(tg, f"_{kind}_deadline")
            getattr(tg, shutdown)()

            getattr(tg, method)()
            tg.reset_inactivity_timer()

            assert timer.call_count == 1
            assert getattr(tg, attribute) is current_timer
            assert getattr(tg, f"_{kind}_deadline") == deadline
            assert expired.is_set()
        finally:
            tg.close()


def test_cancelled_timer_callback_cannot_expire_or_reschedule_after_close(
    timer_kind,
) -> None:
    kind, option, _, _ = timer_kind
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), **{option: 60})
        callback = timer.call_args.args[1]
        tg.close()
        callback()
        assert timer.call_count == 1
        assert not getattr(tg, f"_{kind}_expired").is_set()


@pytest.mark.parametrize("transition", ["restart", "close", "_cancel_timers"])
def test_waiting_timer_callback_rechecks_ownership_after_transition(
    timer_kind, transition
) -> None:
    kind, option, method, _ = timer_kind
    with patch("chat_downloader.utils.timed_generator.threading.Timer") as timer:
        tg = TimedGenerator(iter(()), **{option: 60})
        lock = _ObservedTimerLock("waiting-timer-callback")
        tg._timer_lock = lock
        callback = timer.call_args.args[1]
        callback_thread = threading.Thread(
            target=callback, name="waiting-timer-callback", daemon=True
        )
        try:
            with lock:
                callback_thread.start()
                assert lock.attempted.wait(timeout=1)
                if transition == "restart":
                    getattr(tg, method)()
                else:
                    getattr(tg, transition)()
            callback_thread.join(timeout=1)

            assert not callback_thread.is_alive()
            assert not getattr(tg, f"_{kind}_expired").is_set()
            assert timer.call_count == (2 if transition == "restart" else 1)
        finally:
            tg.close()
            callback_thread.join(timeout=1)


@pytest.mark.parametrize(
    ("option", "operation"),
    [
        ("timeout", "start_timer"),
        ("inactivity_timeout", "start_inactivity_timer"),
        ("inactivity_timeout", "callback"),
    ],
)
def test_timer_creation_racing_close_leaves_no_live_timer(option, operation) -> None:
    attribute = "timer" if option == "timeout" else "inactivity_timer"
    tg = TimedGenerator(iter(()))
    setattr(tg, option, 60)
    lock = _ObservedTimerLock("closing-timed-generator")
    tg._timer_lock = lock
    if operation == "callback":
        with patch("chat_downloader.utils.timed_generator.threading.Timer") as initial:
            tg.start_inactivity_timer()
            start_operation = initial.call_args.args[1]
    else:
        start_operation = getattr(tg, operation)
    timer = threading.Timer(60, lambda: None)
    timer.daemon = True
    constructing_timer = threading.Event()
    allow_construction = threading.Event()

    def construct_timer(_interval, callback):
        timer.function = callback
        constructing_timer.set()
        allow_construction.wait(timeout=5)
        return timer

    starter = threading.Thread(target=start_operation, daemon=True)
    closer = threading.Thread(
        target=tg.close, name="closing-timed-generator", daemon=True
    )
    try:
        with patch(
            "chat_downloader.utils.timed_generator.threading.Timer",
            side_effect=construct_timer,
        ) as factory:
            starter.start()
            assert constructing_timer.wait(timeout=1)
            closer.start()
            assert lock.attempted.wait(timeout=1)
            allow_construction.set()
            starter.join(timeout=1)
            closer.join(timeout=1)

            assert not starter.is_alive()
            assert not closer.is_alive()
            assert getattr(tg, attribute) is timer
            assert factory.call_count == 1
            timer.join(timeout=1)
            assert not timer.is_alive()
    finally:
        allow_construction.set()
        for thread in (starter, closer):
            if thread.ident is not None:
                thread.join(timeout=1)
        tg.close()
        timer.cancel()
        if timer.ident is not None:
            timer.join(timeout=1)


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
    if kind == "inactivity":
        tg._inactivity_deadline = time.monotonic() - 1
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


@pytest.mark.timeout(5)
@pytest.mark.parametrize("error_type", [SystemExit, GeneratorExit, ValueError])
@pytest.mark.parametrize("timeouts", [{}, {"timeout": 60}, {"inactivity_timeout": 60}])
def test_worker_transfers_exit_exceptions_to_consumer(error_type, timeouts) -> None:
    def source():
        yield "first"
        raise error_type("source exit")

    tg = TimedGenerator(source(), **timeouts)
    try:
        assert next(tg) == "first"
        with pytest.raises(error_type, match="source exit"):
            next(tg)
        with pytest.raises(StopIteration):
            next(tg)
        assert tg._closed
    finally:
        tg.close()
    assert not tg._worker.is_alive()


def test_source_failure_retains_priority_when_cleanup_fails(monkeypatch):
    class Source:
        close_calls = 0

        def __next__(self):
            raise ValueError("source failed")

        def close(self):
            self.close_calls += 1
            raise OSError("cleanup failed")

    logs = []
    monkeypatch.setattr(_debugging, "log", lambda level, message: logs.append(message))
    source = Source()
    tg = TimedGenerator(source, timeout=60)
    try:
        with pytest.raises(ValueError, match="source failed"):
            next(tg)
        with pytest.raises(StopIteration):
            next(tg)
    finally:
        tg.close()
    assert source.close_calls == 1
    assert any("cleanup failed" in line for line in logs)

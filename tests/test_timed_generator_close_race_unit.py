# SPDX-License-Identifier: MIT

from __future__ import annotations

import threading

from chat_downloader.utils.timed_generator import TimedGenerator


class BlockingCloseableIterator:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.entered.set()
        self.release.wait(timeout=2)
        return "late"

    def close(self) -> None:
        self.closed.set()


class QueueBlockingIterator:
    def __init__(self) -> None:
        self.second_item_requested = threading.Event()
        self.closed = threading.Event()
        self.calls = 0
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.calls += 1
        if self.calls >= 2:
            self.second_item_requested.set()
        return self.calls

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class BlockingFailingCloseableIterator(BlockingCloseableIterator):
    def __next__(self):
        self.entered.set()
        self.release.wait(timeout=5)
        raise RuntimeError("source failed during shutdown")


class StopBetweenLoopAndAdvance:
    def __init__(self) -> None:
        self.checks = 0

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks >= 2


def test_cancel_timers_defers_close_while_worker_is_advancing() -> None:
    source = BlockingCloseableIterator()
    tg = TimedGenerator(source)

    assert source.entered.wait(timeout=1)

    tg._cancel_timers()

    assert not source.closed.is_set()

    source.release.set()
    tg._worker.join(timeout=1)

    assert source.closed.is_set()


def test_close_defers_source_cleanup_until_failing_next_unwinds() -> None:
    source = BlockingFailingCloseableIterator()
    tg = TimedGenerator(source)

    assert source.entered.wait(timeout=1)

    tg.close()

    assert tg._worker.is_alive()
    assert not source.closed.is_set()

    source.release.set()
    tg._worker.join(timeout=1)

    assert not tg._worker.is_alive()
    assert source.closed.is_set()


def test_close_unblocks_worker_waiting_to_publish_result() -> None:
    source = QueueBlockingIterator()
    tg = TimedGenerator(source)

    assert source.second_item_requested.wait(timeout=1)
    tg.close()
    tg._worker.join(timeout=1)

    assert not tg._worker.is_alive()
    assert source.closed.is_set()
    assert source.close_calls == 1


def test_worker_rechecks_stop_before_advancing_source() -> None:
    tg = TimedGenerator(iter(()))
    tg._worker.join(timeout=1)
    source = QueueBlockingIterator()
    tg.generator = source
    tg._stop_requested = StopBetweenLoopAndAdvance()

    tg._worker_loop()

    assert source.calls == 0
    assert source.close_calls == 1


def test_cancel_timers_suppresses_reentrant_generator_close_error(
    monkeypatch,
) -> None:
    tg = TimedGenerator(iter(()))

    class ReentrantClose:
        def close(self) -> None:
            raise ValueError("generator already executing")

    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "chat_downloader.debugging.log",
        lambda level, message: logs.append((level, str(message))),
    )

    tg.generator = ReentrantClose()
    tg._cancel_timers()

    assert logs == []


def test_cancel_timers_still_logs_other_close_errors(monkeypatch) -> None:
    tg = TimedGenerator(iter(()))

    class BrokenClose:
        def close(self) -> None:
            raise RuntimeError("boom")

    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "chat_downloader.debugging.log",
        lambda level, message: logs.append((level, str(message))),
    )

    tg.generator = BrokenClose()
    tg._cancel_timers()

    assert logs == [("debug", "Suppressed generator close() error: boom")]

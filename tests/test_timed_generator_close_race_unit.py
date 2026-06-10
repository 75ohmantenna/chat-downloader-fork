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


def test_cancel_timers_defers_close_while_worker_is_advancing() -> None:
    source = BlockingCloseableIterator()
    tg = TimedGenerator(source)

    assert source.entered.wait(timeout=1)

    tg._cancel_timers()

    assert not source.closed.is_set()

    source.release.set()
    tg._worker.join(timeout=1)

    assert source.closed.is_set()


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

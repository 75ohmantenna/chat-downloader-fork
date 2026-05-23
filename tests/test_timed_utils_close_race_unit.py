# SPDX-License-Identifier: MIT

from chat_downloader.utils.timed_utils import TimedGenerator


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

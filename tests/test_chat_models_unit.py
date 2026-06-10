# SPDX-License-Identifier: MIT

from __future__ import annotations

import csv
import dataclasses
from dataclasses import dataclass, field
from typing import Any

import pytest

from chat_downloader.errors import NoChatReplay
from chat_downloader.models import get_field_default
from chat_downloader.sites.models import Chat
from chat_downloader.sites.output_dispatch import _ChatOutputDispatcher


def test_chat_output_dispatcher_close_is_idempotent_and_reports_error(
    monkeypatch,
) -> None:
    class Writer:
        file_name = "x"
        output_mode = "raw"

        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.close_calls = 0

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            return None

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            del item, flush

        def close(self) -> None:
            self.close_calls += 1
            if self.error:
                raise self.error

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    writer = Writer(RuntimeError("boom"))
    dispatcher.attach_writer(writer)
    logs: list[str] = []

    monkeypatch.setattr(
        "chat_downloader.sites.output_dispatch.log",
        lambda _level, message: logs.append(message),
    )

    dispatcher.close()

    dispatcher.close()
    assert writer.close_calls == 1
    assert any("Suppressed close() error" in message for message in logs)
    assert any("boom" in message for message in logs)


def test_chat_output_dispatcher_close_reports_all_writer_failures(
    monkeypatch,
) -> None:
    class Writer:
        file_name = "x"
        output_mode = "raw"

        def __init__(self, error: Exception) -> None:
            self.error = error

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            return None

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            del item, flush

        def close(self) -> None:
            raise self.error

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    logs: list[str] = []
    writer_calls = {"a": 0, "b": 0}

    class WriterB(Writer):
        def __init__(self, error: Exception, key: str) -> None:
            super().__init__(error)
            self.key = key

        def close(self) -> None:
            writer_calls[self.key] += 1
            raise self.error

    dispatcher.attach_writer(WriterB(RuntimeError("a"), "a"))
    dispatcher.attach_writer(WriterB(OSError("b"), "b"))
    monkeypatch.setattr(
        "chat_downloader.sites.output_dispatch.log",
        lambda _level, message: logs.append(message),
    )

    dispatcher.close()

    assert writer_calls["a"] == 1
    assert writer_calls["b"] == 1
    assert len(logs) == 2
    assert any("Suppressed close() error" in message for message in logs)


def test_chat_close_suppresses_writer_close_failures(monkeypatch) -> None:
    class Writer:
        file_name = "x"
        output_mode = "raw"

        def __init__(self, error: Exception) -> None:
            self.error = error
            self.closed = False

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            return None

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            del item, flush

        def close(self) -> None:
            self.closed = True
            raise self.error

    logs: list[str] = []
    writer = Writer(RuntimeError("writer close failed"))
    chat = Chat(iter(()), title="Example")
    chat.attach_writer(writer)

    monkeypatch.setattr(
        "chat_downloader.sites.output_dispatch.log",
        lambda _level, message: logs.append(message),
    )

    chat.close()

    assert writer.closed
    assert any("Suppressed close() error" in message for message in logs)
    assert any("writer close failed" in message for message in logs)


def test_chat_next_without_generator_and_print_formatted(monkeypatch) -> None:
    printed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "chat_downloader.sites.models.safe_print",
        lambda message, flush=True: printed.append((message, flush)),
    )

    chat = Chat(None, title="Example")
    chat.set_formatter(lambda item: f"formatted:{item['message']}")

    with pytest.raises(StopIteration, match="No chat generator available"):
        next(chat)

    chat.print_formatted({"message": "hello"}, flush=False)
    assert printed == [("formatted:hello", False)]
    assert chat._seen_message_cache.evictions == 0


def test_chat_next_suppresses_close_error_while_preserving_generator_error(
    monkeypatch,
) -> None:
    logs: list[str] = []

    def broken_generator():
        raise NoChatReplay("original")
        yield  # pragma: no cover

    chat = Chat(broken_generator(), title="Example")
    monkeypatch.setattr(
        chat, "close", lambda: (_ for _ in ()).throw(RuntimeError("close"))
    )
    monkeypatch.setattr(
        "chat_downloader.sites.models.log",
        lambda _level, message: logs.append(message),
    )

    with pytest.raises(NoChatReplay, match="original"):
        next(chat)

    assert any("Suppressed close() error" in message for message in logs)


def test_chat_next_preserves_primary_error_with_multiple_writer_failures(
    monkeypatch,
) -> None:
    logs: list[str] = []

    class Writer:
        file_name = "x"
        output_mode = "raw"

        def __init__(self, error: Exception) -> None:
            self.error = error

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            return None

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            del item, flush

        def close(self) -> None:
            raise self.error

    def broken_generator():
        raise NoChatReplay("original")
        yield  # pragma: no cover

    chat = Chat(broken_generator(), title="Example")
    chat.attach_writer(Writer(RuntimeError("writer one failed")))
    chat.attach_writer(Writer(ValueError("writer two failed")))

    # RuntimeError is caught+logged by the dispatcher (output_dispatch.log);
    # ValueError is not in the dispatcher's except tuple so it propagates to
    # Chat.__next__'s inner close guard, which logs via models.log.
    log_fn = lambda _level, message: logs.append(str(message))  # noqa: E731
    monkeypatch.setattr("chat_downloader.sites.output_dispatch.log", log_fn)
    monkeypatch.setattr("chat_downloader.sites.models.log", log_fn)

    with pytest.raises(NoChatReplay, match="original"):
        next(chat)

    assert any("Suppressed close() error" in message for message in logs)
    assert any("writer one failed" in message for message in logs)
    assert any("writer two failed" in message for message in logs)


def test_pre_initialised_writer_receives_emit_callback() -> None:
    """An already-initialised writer must still receive emitted items.

    Its callback must be installed even though _initialise_writers
    skips the writer.initialize() call.
    """
    received: list[Any] = []

    class PreInitWriter:
        file_name = "x"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("initialize() must not be called")

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(PreInitWriter())

    dispatcher.emit({"message": "hello"})

    assert len(received) == 1
    assert received[0] == {"message": "hello"}


def test_pre_initialised_writer_callback_not_duplicated_across_emits() -> None:
    """Multiple emit() calls must not install the callback more than once."""
    received: list[Any] = []

    class PreInitWriter:
        file_name = "x"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            pass

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(PreInitWriter())

    dispatcher.emit({"message": "first"})
    dispatcher.emit({"message": "second"})

    assert len(received) == 2


@pytest.mark.parametrize(
    "exc",
    [
        OSError("io failure"),
        RuntimeError("runtime failure"),
        csv.Error("csv failure"),
    ],
    ids=["OSError", "RuntimeError", "csv.Error"],
)
def test_chat_output_dispatcher_close_suppresses_known_writer_errors(
    monkeypatch, exc: Exception
) -> None:
    class Writer:
        file_name = "x"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            return None

        def write(
            self, item: dict[str, Any] | str, flush: bool = False
        ) -> None:
            del item, flush

        def close(self) -> None:
            raise exc

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(Writer())
    logs: list[str] = []
    monkeypatch.setattr(
        "chat_downloader.sites.output_dispatch.log",
        lambda _level, message: logs.append(message),
    )

    dispatcher.close()

    assert any("Suppressed close() error" in m for m in logs)


def test_get_field_default_with_default_factory() -> None:
    @dataclass
    class _Model:
        items: list = field(default_factory=list)

    f = dataclasses.fields(_Model)[0]
    result = get_field_default(f)
    assert result == []
    assert isinstance(result, list)

# SPDX-License-Identifier: MIT

from __future__ import annotations

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

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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


def test_chat_close_closes_message_source_once() -> None:
    class CloseableIterator:
        def __init__(self) -> None:
            self.close_calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            return {"message": "waiting"}

        def close(self) -> None:
            self.close_calls += 1

    source = CloseableIterator()
    chat = Chat(source, title="Example")

    chat.close()
    chat.close()

    assert source.close_calls == 1


def test_chat_close_suppresses_known_generator_close_error(monkeypatch) -> None:
    class BrokenIterator:
        def __iter__(self):
            return self

        def __next__(self):
            return {"message": "waiting"}

        @staticmethod
        def close() -> None:
            raise OSError("socket close failed")

    logs: list[str] = []
    monkeypatch.setattr(
        "chat_downloader.sites.models.log",
        lambda _level, message: logs.append(str(message)),
    )

    Chat(BrokenIterator(), title="Example").close()

    assert any("socket close failed" in message for message in logs)


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

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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


def test_pre_initialised_writer_receives_emitted_item() -> None:
    """An already-initialized writer must still receive emitted items."""
    received: list[Any] = []

    class PreInitWriter:
        file_name = "x"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("initialize() must not be called")

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(PreInitWriter())

    dispatcher.emit({"message": "hello"})

    assert len(received) == 1
    assert received[0] == {"message": "hello"}


def test_pre_initialised_writer_receives_each_emit_once() -> None:
    """Multiple emit() calls must each dispatch once."""
    received: list[Any] = []

    class PreInitWriter:
        file_name = "x"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            pass

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(PreInitWriter())

    dispatcher.emit({"message": "first"})
    dispatcher.emit({"message": "second"})

    assert len(received) == 2


def test_formatted_deduplication_is_shared_across_formatted_writers() -> None:
    """Every formatted writer receives the accepted semantic message."""
    formatted_a: list[Any] = []
    formatted_b: list[Any] = []
    raw_items: list[Any] = []
    format_calls: list[dict[str, Any]] = []

    class Writer:
        file_name = "x"

        def __init__(self, output_mode: str, received: list[Any]) -> None:
            self.output_mode = output_mode
            self.received = received

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("already initialized")

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            assert flush is True
            self.received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")

    def format_item(item: dict[str, Any]) -> str:
        format_calls.append(item)
        return f"{item['message_type']}:{item['message_id']}"

    chat.set_formatter(format_item)
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(Writer("formatted", formatted_a))
    dispatcher.attach_writer(Writer("raw", raw_items))
    dispatcher.attach_writer(Writer("formatted", formatted_b))
    paid = {"message_type": "paid_message", "message_id": "paid-1"}
    ticker = {
        "message_type": "ticker_paid_message_item",
        "message_id": "paid-1",
    }

    dispatcher.emit(paid)
    dispatcher.emit(ticker)

    assert formatted_a == ["paid_message:paid-1"]
    assert formatted_b == ["paid_message:paid-1"]
    assert raw_items == [paid, ticker]
    assert format_calls == [paid]
    assert dispatcher.writer_summaries == [
        {
            "file_name": "x",
            "records_written": 1,
        },
        {"file_name": "x", "records_written": 2},
        {
            "file_name": "x",
            "records_written": 1,
        },
    ]


def test_writer_summary_does_not_count_failed_write() -> None:
    """Only completed writer calls contribute to the debug record count."""

    class Writer:
        file_name = "failed.jsonl"
        output_mode = "raw"

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("already initialized")

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            del item, flush
            raise OSError("disk full")

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(Writer())

    with pytest.raises(OSError, match="disk full"):
        dispatcher.emit({"message": "not written"})

    assert dispatcher.writer_summaries == [
        {
            "file_name": "failed.jsonl",
            "records_written": 0,
        }
    ]


def test_raw_only_output_does_not_populate_formatted_dedup_cache() -> None:
    """A formatted writer attached later can accept its first semantic event."""
    raw_items: list[Any] = []
    formatted_items: list[Any] = []

    class Writer:
        file_name = "x"

        def __init__(self, output_mode: str, received: list[Any]) -> None:
            self.output_mode = output_mode
            self.received = received

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("already initialized")

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            self.received.append(item)

        def close(self) -> None:
            pass

    chat = Chat(iter(()), title="Example")
    chat.set_formatter(lambda item: str(item["message_type"]))
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(Writer("raw", raw_items))
    dispatcher.emit({"message_type": "paid_message", "message_id": "paid-1"})
    dispatcher.attach_writer(Writer("formatted", formatted_items))
    dispatcher.emit(
        {"message_type": "ticker_paid_message_item", "message_id": "paid-1"}
    )

    assert len(raw_items) == 2
    assert formatted_items == ["ticker_paid_message_item"]


def test_attaching_same_writer_twice_is_idempotent() -> None:
    writes: list[Any] = []

    class Writer:
        file_name = "x"
        output_mode = "raw"

        def __init__(self) -> None:
            self.close_calls = 0

        def is_initialised(self) -> bool:
            return True

        def initialize(self) -> None:
            raise AssertionError("already initialized")

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            writes.append(item)

        def close(self) -> None:
            self.close_calls += 1

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    writer = Writer()
    dispatcher.attach_writer(writer)
    dispatcher.attach_writer(writer)

    dispatcher.emit({"message": "once"})
    dispatcher.close()

    assert len(dispatcher.writers) == 1
    assert writes == [{"message": "once"}]
    assert writer.close_calls == 1


def test_emit_without_writers_is_a_noop() -> None:
    """emit() returns early when no writers exist."""
    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)

    dispatcher.emit({"message": "ignored"})

    assert dispatcher.writers == []


@pytest.mark.parametrize(
    "exc",
    [
        OSError("io failure"),
        RuntimeError("runtime failure"),
    ],
    ids=["OSError", "RuntimeError"],
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

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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

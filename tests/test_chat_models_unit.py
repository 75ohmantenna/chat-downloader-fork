# SPDX-License-Identifier: MIT

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import pytest

from chat_downloader.errors import NoChatReplay
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.models import get_field_default
from chat_downloader.sites.models import Chat
from chat_downloader.sites.output_dispatch import _ChatOutputDispatcher


class _Writer:
    """Already-initialized writer with observable writes and close failures."""

    file_name = "x"

    def __init__(
        self,
        output_mode: str = "raw",
        received: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output_mode = output_mode
        self.received = received if received is not None else []
        self.error = error
        self.close_calls = 0

    def is_initialised(self) -> bool:
        return True

    def initialize(self) -> None:
        raise AssertionError("already initialized")

    def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
        assert flush is True
        self.received.append(item)

    def close(self) -> None:
        self.close_calls += 1
        if self.error:
            raise self.error


@pytest.mark.parametrize("target", ["dispatcher", "chat"])
@pytest.mark.parametrize(
    "errors",
    [
        (RuntimeError("boom"),),
        (OSError("io failure"),),
        (RuntimeError("a"), OSError("b")),
    ],
    ids=["runtime-error", "os-error", "multiple-errors"],
)
def test_close_reports_writer_failures_once(monkeypatch, target, errors) -> None:
    chat = Chat(iter(()), title="Example")
    owner = _ChatOutputDispatcher(chat) if target == "dispatcher" else chat
    writers = [_Writer(error=error) for error in errors]
    for writer in writers:
        owner.attach_writer(writer)
    logs: list[str] = []
    monkeypatch.setattr(
        "chat_downloader.sites.output_dispatch.log",
        lambda _level, message: logs.append(message),
    )

    owner.close()
    owner.close()

    assert [writer.close_calls for writer in writers] == [1] * len(writers)
    assert len(logs) == len(errors)
    for error in errors:
        assert any(str(error) in message for message in logs)


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


def test_chat_print_formatted_keeps_output_on_one_physical_line(monkeypatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(
        "chat_downloader.sites.models.safe_print",
        lambda message, flush=True: printed.append(message),
    )
    formatter = ItemFormatter()
    chat = Chat(None, title="Example")
    chat.set_formatter(
        lambda item: formatter.format(
            item,
            format_object={"template": "head\n{message}"},
        )
    )

    chat.print_formatted({"message": "first\u2028second\x9bhidden"})

    assert printed == [r"head\nfirst\u2028secondhidden"]
    assert len(printed[0].splitlines()) == 1


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

    def broken_generator():
        raise NoChatReplay("original")
        yield  # pragma: no cover

    chat = Chat(broken_generator(), title="Example")
    chat.attach_writer(_Writer(error=RuntimeError("writer one failed")))
    chat.attach_writer(_Writer(error=ValueError("writer two failed")))

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


@pytest.mark.parametrize("messages", [["hello"], ["first", "second"]])
def test_pre_initialised_writer_receives_each_emit_once(messages) -> None:
    writer = _Writer()
    dispatcher = _ChatOutputDispatcher(Chat(iter(()), title="Example"))
    dispatcher.attach_writer(writer)
    items = [{"message": message} for message in messages]

    for item in items:
        dispatcher.emit(item)

    assert writer.received == items


def test_formatted_deduplication_is_shared_across_formatted_writers() -> None:
    """Every formatted writer receives the accepted semantic message."""
    formatted_a: list[Any] = []
    formatted_b: list[Any] = []
    raw_items: list[Any] = []
    format_calls: list[dict[str, Any]] = []

    chat = Chat(iter(()), title="Example")

    def format_item(item: dict[str, Any]) -> str:
        format_calls.append(item)
        return f"{item['message_type']}:{item['message_id']}"

    chat.set_formatter(format_item)
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(_Writer("formatted", formatted_a))
    dispatcher.attach_writer(_Writer("raw", raw_items))
    dispatcher.attach_writer(_Writer("formatted", formatted_b))
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
    assert dispatcher.formatted_duplicates_suppressed == 1
    assert dispatcher.writer_summaries == [
        {
            "file_name": "x",
            "file_created": True,
            "records_written": 1,
        },
        {"file_name": "x", "file_created": True, "records_written": 2},
        {
            "file_name": "x",
            "file_created": True,
            "records_written": 1,
        },
    ]


def test_writer_summary_does_not_count_failed_write() -> None:
    """Only completed writer calls contribute to the debug record count."""

    class Writer(_Writer):
        file_name = "failed.jsonl"

        def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
            del item, flush
            raise OSError("disk full")

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(Writer())

    with pytest.raises(OSError, match="disk full"):
        dispatcher.emit({"message": "not written"})

    assert dispatcher.writer_summaries == [
        {
            "file_name": "failed.jsonl",
            "file_created": True,
            "records_written": 0,
        }
    ]


def test_raw_only_output_does_not_populate_formatted_dedup_cache() -> None:
    """A formatted writer attached later can accept its first semantic event."""
    raw_items: list[Any] = []
    formatted_items: list[Any] = []

    chat = Chat(iter(()), title="Example")
    chat.set_formatter(lambda item: str(item["message_type"]))
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(_Writer("raw", raw_items))
    dispatcher.emit({"message_type": "paid_message", "message_id": "paid-1"})
    dispatcher.attach_writer(_Writer("formatted", formatted_items))
    dispatcher.emit(
        {"message_type": "ticker_paid_message_item", "message_id": "paid-1"}
    )

    assert len(raw_items) == 2
    assert formatted_items == ["ticker_paid_message_item"]
    assert dispatcher.formatted_duplicates_suppressed == 0


def test_raw_only_output_does_not_count_formatted_suppressions() -> None:
    """Raw duplicates remain lossless and do not inflate formatted stats."""
    raw_items: list[Any] = []

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    dispatcher.attach_writer(_Writer("raw", raw_items))
    paid = {"message_type": "paid_message", "message_id": "paid-1"}
    ticker = {
        "message_type": "ticker_paid_message_item",
        "message_id": "paid-1",
    }

    dispatcher.emit(paid)
    dispatcher.emit(ticker)

    assert raw_items == [paid, ticker]
    assert dispatcher.formatted_duplicates_suppressed == 0


def test_attaching_same_writer_twice_is_idempotent() -> None:
    writes: list[Any] = []

    chat = Chat(iter(()), title="Example")
    dispatcher = _ChatOutputDispatcher(chat)
    writer = _Writer(received=writes)
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


def test_get_field_default_with_default_factory() -> None:
    @dataclass
    class _Model:
        items: list = field(default_factory=list)

    f = dataclasses.fields(_Model)[0]
    result = get_field_default(f)
    assert result == []
    assert isinstance(result, list)

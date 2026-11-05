# SPDX-License-Identifier: MIT
from __future__ import annotations

import pytest

from chat_downloader.errors import NoChatReplay
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.output_dispatch import _ChatOutputDispatcher


class _Writer:
    file_name = "x"

    def __init__(self, output_mode="raw", error=None):
        self.output_mode, self.error = output_mode, error
        self.received = []
        self.close_calls = 0

    def is_initialised(self):
        return True

    def initialize(self):
        raise AssertionError("already initialized")

    def write(self, item, flush=False):
        assert flush is True
        self.received.append(item)

    def close(self):
        self.close_calls += 1
        if self.error:
            raise self.error


class _CloseableSource:
    def __init__(self, items=(), error=None):
        self.items = iter(items)
        self.error = error
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.items)

    def close(self):
        self.close_calls += 1
        if self.error is not None:
            raise self.error


@pytest.fixture
def logs(monkeypatch):
    messages = []
    for module in ("models", "output_dispatch"):
        monkeypatch.setattr(
            f"chat_downloader.sites.{module}.log",
            lambda _level, message: messages.append(str(message)),
        )
    return messages


@pytest.fixture
def dispatcher():
    return _ChatOutputDispatcher(Chat(iter(()), title="Example"))


@pytest.mark.parametrize(
    ("target", "errors"),
    [
        (target, errors)
        for target in ("dispatcher", "chat")
        for errors in [
            (RuntimeError("boom"),),
            (OSError("io failure"),),
            (RuntimeError("a"), OSError("b")),
        ]
    ]
    + [("source", (error,)) for error in (None, OSError("socket close failed"))],
)
def test_close_reports_writer_failures_once(dispatcher, logs, target, errors):
    class Source(_Writer):
        def __iter__(self):
            return self

        def __next__(self):
            return {"message": "waiting"}

    if target == "source":
        writers = [Source(error=errors[0])]
        owner = Chat(writers[0])
    else:
        owner = dispatcher if target == "dispatcher" else Chat(iter(()))
        writers = [_Writer(error=error) for error in errors]
        for writer in writers:
            owner.attach_writer(writer)
    owner.close()
    owner.close()
    assert [writer.close_calls for writer in writers] == [1] * len(writers)
    errors = [error for error in errors if error is not None]
    assert len(logs) == len(errors)
    assert all(any(str(error) in line for line in logs) for error in errors)


@pytest.mark.parametrize(
    ("template", "message", "flush", "expected"),
    [
        ("formatted:{message}", "hello", False, "formatted:hello"),
        (
            "head\n{message}",
            "first\u2028second\x9bhidden",
            True,
            r"head\nfirst\u2028secondhidden",
        ),
    ],
)
def test_missing_source_and_single_line_formatted_print(
    monkeypatch, template, message, flush, expected
):
    printed = []
    monkeypatch.setattr(
        "chat_downloader.sites.models.safe_print",
        lambda message, flush=True: printed.append((message, flush)),
    )
    formatter = ItemFormatter()
    chat = Chat(None)
    chat.set_formatter(
        lambda item: formatter.format(item, format_object={"template": template})
    )
    with pytest.raises(StopIteration, match="No chat generator available"):
        next(chat)
    chat.print_formatted({"message": message}, flush=flush)
    assert printed == [(expected, flush)]
    assert len(printed[0][0].splitlines()) == 1


@pytest.mark.parametrize("writer_failures", [False, True])
def test_next_preserves_primary_error_when_close_fails(
    monkeypatch, logs, writer_failures
):
    def broken_generator():
        raise NoChatReplay("original")
        yield  # pragma: no cover

    chat = Chat(broken_generator())
    if writer_failures:
        chat.attach_writer(_Writer(error=RuntimeError("writer one failed")))
        chat.attach_writer(_Writer(error=ValueError("writer two failed")))
    else:
        monkeypatch.setattr(
            chat, "close", lambda: (_ for _ in ()).throw(RuntimeError("close"))
        )
    with pytest.raises(NoChatReplay, match="original"):
        next(chat)
    assert any("Suppressed close() error" in line for line in logs)
    if writer_failures:
        for message in ("writer one failed", "writer two failed"):
            assert any(message in line for line in logs)


@pytest.mark.parametrize("messages", [["hello"], ["first", "second"]])
def test_pre_initialised_writer_and_duplicate_attachment(dispatcher, messages):
    writer = _Writer()
    dispatcher.attach_writer(writer)
    dispatcher.attach_writer(writer)
    items = [{"message": message} for message in messages]
    for item in items:
        dispatcher.emit(item)
    dispatcher.close()
    assert writer.received == items
    assert len(dispatcher.writers) == 1
    assert writer.close_calls == 1


@pytest.mark.parametrize("mode", ["formatted", "raw", "late-formatted", "none"])
def test_semantic_deduplication_is_shared_but_raw_output_is_lossless(mode):
    chat = Chat(iter(()))
    calls = []

    def format_item(item):
        calls.append(item)
        return f"{item['message_type']}:{item['message_id']}"

    chat.set_formatter(format_item)
    dispatcher = _ChatOutputDispatcher(chat)
    raw, first, second = _Writer(), _Writer("formatted"), _Writer("formatted")
    if mode == "formatted":
        dispatcher.attach_writer(first)
    if mode != "none":
        dispatcher.attach_writer(raw)
    if mode == "formatted":
        dispatcher.attach_writer(second)
    paid = {"message_type": "paid_message", "message_id": "paid-1"}
    ticker = {"message_type": "ticker_paid_message_item", "message_id": "paid-1"}
    dispatcher.emit(paid)
    if mode == "late-formatted":
        dispatcher.attach_writer(first)
    dispatcher.emit(ticker)
    assert raw.received == ([] if mode == "none" else [paid, ticker])
    assert dispatcher.formatted_duplicates_suppressed == (
        1 if mode == "formatted" else 0
    )
    if mode == "formatted":
        assert first.received == second.received == ["paid_message:paid-1"]
        assert calls == [paid]
        assert dispatcher.writer_summaries == [
            {"file_name": "x", "file_created": True, "records_written": count}
            for count in (1, 2, 1)
        ]
    elif mode == "late-formatted":
        assert first.received == ["ticker_paid_message_item:paid-1"]
        assert calls == [ticker]
    else:
        assert calls == []


def test_writer_summary_does_not_count_failed_write(dispatcher):
    class Writer(_Writer):
        file_name = "failed.jsonl"

        def write(self, item, flush=False):
            raise OSError("disk full")

    dispatcher.attach_writer(Writer())
    with pytest.raises(OSError, match="disk full"):
        dispatcher.emit({"message": "not written"})
    assert dispatcher.writer_summaries == [
        {"file_name": "failed.jsonl", "file_created": True, "records_written": 0}
    ]


@pytest.mark.parametrize("exit_kind", ["unused", "break", "exhausted", "explicit"])
def test_chat_context_closes_source_and_writers_once(exit_kind):
    items = [{"message": "first"}, {"message": "second"}]
    source = _CloseableSource(items)
    writer = _Writer()
    chat = Chat(source)
    chat.attach_writer(writer)

    with chat as managed:
        assert managed is chat
        if exit_kind == "break":
            for item in managed:
                assert item == items[0]
                break
        elif exit_kind == "exhausted":
            assert list(managed) == items
            assert source.close_calls == writer.close_calls == 1
        elif exit_kind == "explicit":
            managed.close()

    chat.close()
    assert source.close_calls == writer.close_calls == 1
    assert writer.received == (
        items[:1] if exit_kind == "break" else items if exit_kind == "exhausted" else []
    )


@pytest.mark.parametrize(
    "consumer_error", [RuntimeError("consumer"), KeyboardInterrupt(), SystemExit(7)]
)
@pytest.mark.parametrize("cleanup_failure", [None, "source", "writer"])
def test_chat_context_preserves_consumer_error(consumer_error, cleanup_failure, logs):
    source = _CloseableSource(
        [{"message": "first"}],
        error=LookupError("source cleanup") if cleanup_failure == "source" else None,
    )
    writer = _Writer(
        error=ValueError("writer cleanup") if cleanup_failure == "writer" else None
    )
    chat = Chat(source)
    chat.attach_writer(writer)

    with pytest.raises(type(consumer_error)) as raised, chat:
        assert next(chat) == {"message": "first"}
        raise consumer_error

    assert raised.value is consumer_error
    chat.close()
    assert source.close_calls == writer.close_calls == 1
    if cleanup_failure is not None:
        assert any(
            "Suppressed close() error" in message
            and f"{cleanup_failure} cleanup" in message
            for message in logs
        )


@pytest.mark.parametrize("cleanup_failure", ["source", "writer"])
def test_chat_context_propagates_unexpected_cleanup_error_without_body_error(
    cleanup_failure,
):
    cleanup_error = LookupError("cleanup")
    source = _CloseableSource(
        error=cleanup_error if cleanup_failure == "source" else None
    )
    writer = _Writer(error=cleanup_error if cleanup_failure == "writer" else None)
    chat = Chat(source)
    chat.attach_writer(writer)

    with pytest.raises(LookupError) as raised, chat:
        pass

    assert raised.value is cleanup_error
    chat.close()
    assert source.close_calls == writer.close_calls == 1

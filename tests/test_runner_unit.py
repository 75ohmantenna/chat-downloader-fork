# SPDX-License-Identifier: MIT

from __future__ import annotations

import ast
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from requests.exceptions import (
    ConnectionError,  # noqa: A004 — requests.ConnectionError is intentional here
    RequestException,
)

from chat_downloader.debugging import TestingException as RuntimeTestingException
from chat_downloader.errors import ChatGeneratorError, ParsingError, SiteNotSupported
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.runtime.runner import (
    SITE_CHANGE_ERROR_HINT,
    RunResult,
    _log_run_summary,
    create_message_callback,
    execute_run,
)
from chat_downloader.sites.models import Chat
from chat_downloader.utils.timed_generator import TimedGenerator


class _FakeChat:
    def __init__(self, items=(), *, error=None, close_error=None) -> None:
        self.items = iter(items)
        self.error = error
        self.close_error = close_error
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.items)
        except StopIteration:
            if self.error is not None:
                raise self.error from None
            raise

    def print_formatted(self, _message) -> None: ...

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture
def downloader():
    """Build a downloader around a supplied chat or acquisition failure."""

    def make(chat=None, *, error=None, close_error=None):
        class Downloader(_FakeChat):
            def __init__(self, **kwargs):
                super().__init__(close_error=close_error)
                Downloader.instance = self
                self.get_chat = MagicMock(
                    side_effect=error,
                    return_value=chat if chat is not None else _FakeChat(),
                )

        return Downloader

    return make


@pytest.fixture
def logged(monkeypatch):
    entries = []
    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: entries.append((level, str(message))),
    )
    return entries


def _summary(logged):
    return ast.literal_eval(
        next(
            message.removeprefix("Run summary: ")
            for level, message in logged
            if level == "debug" and message.startswith("Run summary: ")
        )
    )


def test_run_result_preserves_existing_positional_field_order() -> None:
    result = RunResult(True, 7, True, "stopped")

    assert result.success is True
    assert result.message_count == 7
    assert result.interrupted is True
    assert result.error_message == "stopped"
    assert result.message_type_counts == {}


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (ChatGeneratorError("gen"), SITE_CHANGE_ERROR_HINT),
        (ParsingError("parse"), SITE_CHANGE_ERROR_HINT),
        (RuntimeTestingException("test"), SITE_CHANGE_ERROR_HINT),
        (ConnectionError("offline"), "internet connection"),
        (SiteNotSupported("no site"), "no site"),
        (RequestException("timeout"), "timeout"),
        (OSError("disk full"), "disk full"),
        (ValueError("max_attempts must be positive"), "max_attempts"),
        (RuntimeError("unexpected acquisition"), "unexpected acquisition"),
        (KeyboardInterrupt(), "Keyboard Interrupt"),
    ],
)
def test_execute_run_reports_acquisition_errors(downloader, logged, error, fragment):
    factory = downloader(error=error)
    result = execute_run(factory)

    assert result.success is False
    assert result.error_message is not None
    assert fragment in result.error_message
    assert result.interrupted is isinstance(error, KeyboardInterrupt)
    assert [(level, message) for level, message in logged if level == "error"] == [
        ("error", result.error_message)
    ]
    assert _summary(logged)["success"] is False
    assert factory.instance.closed is True


_PAID_MESSAGES = [
    ("paid_message", "one"),
    ("ticker_paid_message_item", "one"),
    ("membership_item", "two"),
    ("ticker_sponsor_item", "two"),
    ("paid_message", "one"),
]


@pytest.mark.parametrize(
    ("limit", "messages", "emitted"),
    [
        (
            None,
            [
                ("paid_message", "a"),
                ("ticker_paid_message_item", "a"),
                ("text_message", "a"),
            ],
            [0, 2],
        ),
        (1, _PAID_MESSAGES, [0, 2, 4]),
        (
            0,
            [("paid_message", "dup"), ("text_message", "dup"), ("paid_message", "dup")],
            [0, 1],
        ),
    ],
)
def test_create_message_callback_deduplicates(limit, messages, emitted) -> None:
    chat = MagicMock()
    options = {} if limit is None else {"max_seen_message_ids": limit}
    callback = create_message_callback(quiet=False, chat=chat, **options)
    items = [{"message_type": kind, "message_id": key} for kind, key in messages]

    for item in items:
        callback(item)

    assert [call.args[0] for call in chat.print_formatted.call_args_list] == [
        items[index] for index in emitted
    ]


@pytest.mark.parametrize(
    ("cache_size", "quiet", "emitted", "empty"),
    [
        (1, False, 3, False),
        (2, True, 2, False),
        (2, True, 0, True),
    ],
)
def test_execute_run_logs_final_message_and_writer_counts(
    downloader, logged, tmp_path, capsys, cache_size, quiet, emitted, empty
):
    items = (
        []
        if empty
        else [{"message_type": kind, "message_id": key} for kind, key in _PAID_MESSAGES]
    )
    chat = Chat(iter(items), max_seen_message_ids=cache_size)
    chat.set_formatter(lambda item: item["message_id"])
    paths = [tmp_path / "chat.jsonl", tmp_path / "chat.txt"]
    for path in paths:
        chat.attach_writer(ContinuousWriter(str(path), lazy_initialise=empty))

    factory = downloader(chat)
    result = execute_run(factory, quiet=quiet, max_seen_message_ids=cache_size)
    assert factory.instance.closed is True

    assert result.success is True
    assert result.message_count == len(items)
    assert result.message_type_counts == (
        {}
        if empty
        else {
            "paid_message": 2,
            "ticker_paid_message_item": 1,
            "membership_item": 1,
            "ticker_sponsor_item": 1,
        }
    )
    summary = _summary(logged)
    assert summary["message_count"] == result.message_count
    assert summary["message_type_counts"] == result.message_type_counts
    assert summary["formatted_duplicates_suppressed"] == len(items) - emitted
    assert summary["output_writers"] == [
        {"file_name": str(path), "file_created": not empty, "records_written": count}
        for path, count in zip(paths, [len(items), emitted], strict=True)
    ]
    if empty:
        for path in paths:
            assert not path.exists()
            assert any(
                level == "info" and str(path) in message for level, message in logged
            )
    else:
        assert [json.loads(line) for line in paths[0].read_text().splitlines()] == items
        assert paths[1].read_text().splitlines() == ["one", "two", "one"][:emitted]
    expected = ["one", "two", "one"][:emitted]
    assert capsys.readouterr().out.splitlines() == ([] if quiet else expected)


def test_execute_run_marks_deadline_prefetch_summary_incomplete(downloader, logged):
    advance_started = threading.Event()
    allow_item = threading.Event()
    diagnostics: dict[str, object] = {"live_emitted_count": 0}

    def blocked_source():
        advance_started.set()
        allow_item.wait()
        diagnostics["live_emitted_count"] = 1
        yield {"message_type": "text_message", "message_id": "late"}

    timed_source = TimedGenerator(blocked_source(), timeout=0.01)
    try:
        assert advance_started.wait(timeout=1)
        result = execute_run(
            downloader(Chat(timed_source, diagnostics=diagnostics)), quiet=True
        )
        assert result.success is True
        assert result.message_count == 0
        summary = _summary(logged)
        assert summary["prefetched_after_deadline_count"] == 0
        assert summary["deadline_prefetch_count_complete"] is False
        assert summary["provider_diagnostics"] == {"live_emitted_count": 0}
    finally:
        allow_item.set()
        timed_source._worker.join(timeout=1)

    assert timed_source.deadline_prefetch_summary() == (1, True)
    assert diagnostics == {"live_emitted_count": 1}


def test_log_run_summary_redacts_credentials(logged):
    chat = SimpleNamespace(
        _output_dispatcher=SimpleNamespace(
            formatted_duplicates_suppressed=0,
            writer_summaries=[
                {
                    "file_name": "https://alice:hunter2@example.invalid/chat.jsonl",
                    "file_created": False,
                    "records_written": 0,
                }
            ],
        )
    )
    _log_run_summary(chat, 0, {})

    assert {level for level, _message in logged} == {"info", "debug"}
    assert all("hunter2" not in message for _level, message in logged)
    assert all("<redacted>@example.invalid" in message for _level, message in logged)


@pytest.mark.parametrize("message_count", [0, 1])
@pytest.mark.parametrize("propagate", [False, True])
def test_execute_run_interrupt_closes_real_chat_source(
    downloader, logged, message_count, propagate
):
    source = _FakeChat(
        [{"message_type": "text_message", "message_id": "1"}] * message_count,
        error=KeyboardInterrupt(),
    )
    factory = downloader(Chat(source))
    if propagate:
        with pytest.raises(KeyboardInterrupt):
            execute_run(factory, quiet=True, propagate_interrupt=True)
    else:
        result = execute_run(factory, quiet=True)
        assert result.interrupted is True
        assert result.message_count == message_count
        assert ("error", result.error_message) in logged

    assert source.closed is True
    assert factory.instance.closed is True
    assert _summary(logged)["message_count"] == message_count


def test_execute_run_propagates_acquisition_interrupt(downloader):
    factory = downloader(error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        execute_run(factory, propagate_interrupt=True)
    assert factory.instance.closed is True


@pytest.mark.parametrize(
    ("target", "error", "suppressed"),
    [
        ("chat", OSError("close failed"), True),
        ("chat", RuntimeError("programmer bug"), False),
        ("downloader", RuntimeError("downloader cleanup failed"), False),
    ],
)
def test_execute_run_cleanup_without_primary_error(
    downloader, logged, target, error, suppressed
):
    chat = _FakeChat(close_error=error if target == "chat" else None)
    factory = downloader(chat, close_error=error if target == "downloader" else None)
    if suppressed:
        execute_run(factory)
        assert any(
            level == "warning" and str(error) in message for level, message in logged
        )
        assert factory.instance.closed is True
    else:
        result = execute_run(factory)
        assert not result.success
        assert str(error) in result.error_message
        assert factory.instance.closed is True
    assert chat.closed is True


def test_execute_run_preserves_primary_error_when_cleanup_fails(downloader, logged):
    chat = _FakeChat(
        error=ChatGeneratorError("generator failed"),
        close_error=RuntimeError("chat cleanup failed"),
    )
    factory = downloader(chat, close_error=RuntimeError("downloader cleanup failed"))
    result = execute_run(factory)

    assert result.success is False
    assert "generator failed" in result.error_message
    assert SITE_CHANGE_ERROR_HINT in result.error_message
    assert ("error", result.error_message) in logged
    for fragment in ("chat cleanup failed", "downloader cleanup failed"):
        assert any(
            level == "warning" and fragment in message for level, message in logged
        )
    assert chat.closed is True
    assert factory.instance.closed is True


def test_unexpected_chat_close_error_still_writes_manifest(
    downloader, monkeypatch, tmp_path
):
    chat = Chat(iter(()), id="video", status="completed")
    close = MagicMock(side_effect=RuntimeError("close failed"))
    monkeypatch.setattr(chat, "close", close)
    factory = downloader(chat)
    manifest = tmp_path / "run.json"
    result = execute_run(factory, run_manifest=str(manifest))
    assert not result.success
    assert result.error_message == "close failed"
    assert factory.instance.closed
    assert json.loads(manifest.read_text())["success"] is False


def test_execute_run_detects_write_errors(downloader, logged):
    chat = _FakeChat()
    chat.write_error_count = 1
    result = execute_run(downloader(chat))

    assert result.success is False
    assert "output writer(s) reported errors" in result.error_message
    assert _summary(logged)["success"] is False


@pytest.mark.parametrize(
    ("options", "expected_modes"),
    [
        ([{"pause_on_debug": True}], ["PAUSE_ON_DEBUG"]),
        ([{"exit_on_debug": True}, {}], ["EXIT_ON_DEBUG", "NONE"]),
    ],
)
def test_execute_run_configures_and_resets_testing_mode(
    monkeypatch, downloader, options, expected_modes
):
    seen_modes = []
    monkeypatch.setattr(
        "chat_downloader.runtime.runner.set_testing_mode",
        lambda mode: seen_modes.append(mode.name),
    )
    for kwargs in options:
        execute_run(downloader(), **kwargs)
    assert seen_modes == expected_modes

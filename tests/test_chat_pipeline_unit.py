# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from chat_downloader.models import ChatRequest, SiteDefault
from chat_downloader.runtime import chat_pipeline
from chat_downloader.sites.models import Chat

apply_message_limit = chat_pipeline._apply_message_limit
configure_chat = chat_pipeline.configure_chat
configure_formatter = chat_pipeline._configure_formatter
configure_output_writer = chat_pipeline._configure_output_writer
configure_timeouts = chat_pipeline._configure_timeouts


@pytest.mark.parametrize(("limit", "expected"), [(2, [0, 1]), (None, [0, 1, 2, 3, 4])])
def test_message_limit_preserves_selected_items(limit, expected) -> None:
    chat = Chat(iter(range(5)), title="Example")
    apply_message_limit(chat, limit)
    assert list(cast("Any", chat.chat)) == expected


def test_apply_message_limit_propagates_close_to_source() -> None:
    source = MagicMock()
    source.__next__.side_effect = [{"id": "1"}, {"id": "2"}]
    chat = Chat(source, title="Example")

    apply_message_limit(chat, 1)
    assert next(cast("Any", chat.chat)) == {"id": "1"}
    limited = cast("Any", chat.chat)
    chat.close()
    limited.close()

    source.close.assert_called_once()


def test_apply_message_limit_closes_source_when_iteration_raises() -> None:
    source = MagicMock()
    source.__next__.side_effect = KeyboardInterrupt
    chat = Chat(source, title="Example")
    apply_message_limit(chat, 1)

    with pytest.raises(KeyboardInterrupt):
        next(cast("Any", chat.chat))

    source.close.assert_called_once()


@pytest.mark.parametrize(("timeout", "inactivity"), [(5, 7), (None, 7), (5, None)])
def test_timeout_callbacks_record_termination(monkeypatch, timeout, inactivity) -> None:
    class FakeTimedGenerator:
        on_timeout = None
        on_inactivity_timeout = None

        def __init__(self, source, timeout, inactivity_timeout) -> None:
            self.source = source

    chat = Chat(iter(()))
    monkeypatch.setattr(chat_pipeline, "TimedGenerator", FakeTimedGenerator)
    configure_timeouts(chat, timeout, inactivity)
    wrapped = cast("Any", chat.chat)
    for callback, duration, reason in (
        (wrapped.on_timeout, timeout, "timeout"),
        (wrapped.on_inactivity_timeout, inactivity, "inactivity_timeout"),
    ):
        if duration is None:
            assert callback is None
        else:
            callback()
            assert chat.diagnostics["termination_reason"] == reason


@pytest.mark.parametrize("has_source", [True, False])
def test_timeouts_leave_absent_source_or_disabled_deadlines_untouched(
    has_source,
) -> None:
    source = iter(()) if has_source else None
    chat = SimpleNamespace(chat=source)
    configure_timeouts(chat, None if has_source else 30, None)
    assert chat.chat is source


class _FakeSite:
    """Provider-neutral live-format capability."""

    def __init__(
        self,
        *,
        live_statuses: frozenset[str] = frozenset(),
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._live = live_statuses
        self._overrides = overrides or {}

    def is_live_status(self, status: str | None) -> bool:
        return status in self._live

    def resolve_live_format(self, format_name: str) -> str:
        return self._overrides.get(format_name, format_name)


@pytest.mark.parametrize(("status", "format_name", "has_site", "expected"),[
        ("live", "default", True, "live-default:hello"),
        ("live", "custom", True, "live-custom:hello"),
        ("past", "default", True, "default:hello"),
        ("live", "neutral", True, "neutral:hello"),
        ("live", "default", False, "default:hello"),
        ("live", SiteDefault("default"), True, "default:hello"),
    ],
)
def test_formatter_resolves_live_overrides_and_custom_file(
    tmp_path, status, format_name, has_site, expected
) -> None:
    formats = tmp_path / "formats.json"
    formats.write_text(
        json.dumps(
            {
                name: {"template": prefix + ":{message}"}
                for name, prefix in (
                    ("default", "default"),
                    ("custom", "custom"),
                    ("live_default", "live-default"),
                    ("live_custom", "live-custom"),
                    ("neutral", "neutral"),
                )
            }
        )
    )
    chat = Chat(iter(()), status=status)
    if has_site:
        chat.site = cast(
            "Any",
            _FakeSite(
                live_statuses=frozenset({"live"}),
                overrides={"default": "live_default", "custom": "live_custom"},
            ),
        )
    configure_formatter(chat, str(formats), format_name)
    assert chat.format({"message": "hello"}) == expected


def test_output_options_preserve_append_sort_order_and_multiple_formats(
    tmp_path,
) -> None:
    raw, formatted = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    raw.write_text('{"existing": true}\n')
    formatted.write_text("existing\n")
    message = {"z": 1, "a": 2}
    chat = Chat(iter([message]))
    chat.set_formatter(lambda _: "hello")
    request = ChatRequest(
        output=[str(raw), str(formatted)], sort_keys=False, overwrite=False
    )
    configure_output_writer(chat, request)
    assert list(chat) == [message]
    assert raw.read_text() == '{"existing": true}\n{"z": 1, "a": 2}\n'
    assert formatted.read_text() == "existing\nhello\n"


def test_configure_output_writer_deduplicates_duplicate_paths(tmp_path) -> None:
    output_path = str(tmp_path / "out.jsonl")
    aliased_path = f"{tmp_path}/./out.jsonl"
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[output_path, aliased_path],
    )

    chat = Chat(status="live")
    writer_factory = MagicMock(return_value=MagicMock())

    configure_output_writer(chat, request, writer_factory=writer_factory)
    assert len(chat._output_dispatcher.writers) == 1


def test_configure_output_writer_alias_writes_each_item_once(tmp_path) -> None:
    output_path = tmp_path / "out.txt"
    output_path.write_text("existing\n", encoding="utf-8")
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[str(output_path), f"{tmp_path}/./out.txt"],
        overwrite=False,
    )
    chat = Chat(iter([{"message_type": "text_message"}]), status="live")
    chat.set_formatter(lambda _item: "hello")

    configure_output_writer(chat, request)
    assert next(chat) == {"message_type": "text_message"}
    with pytest.raises(StopIteration):
        next(chat)

    assert output_path.read_text(encoding="utf-8") == "existing\nhello\n"


def test_configure_output_writer_deduplicates_expanded_template_paths(
    tmp_path,
) -> None:
    output_path = tmp_path / "same.jsonl"
    output_path.write_text('{"old": true}\n', encoding="utf-8")
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[str(tmp_path / "{title}.jsonl"), str(output_path)],
        overwrite=False,
    )
    chat = Chat(
        iter([{"message_type": "text_message", "message": "hello"}]),
        title="same",
    )

    configure_output_writer(chat, request)
    assert len(chat._output_dispatcher.writers) == 1
    assert next(chat)["message"] == "hello"
    chat.close()

    assert len(output_path.read_text(encoding="utf-8").splitlines()) == 2


def test_configure_output_writer_rejects_json_output(tmp_path) -> None:
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=str(tmp_path / "out.json"),
    )
    chat = Chat(status="live")

    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        configure_output_writer(chat, request)


def test_configure_chat_composes_real_limit_formatter_and_writer(tmp_path) -> None:
    output = tmp_path / "chat.jsonl"
    chat = Chat(
        iter(
            [
                {"message_id": "one", "message_type": "text_message"},
                {"message_id": "two", "message_type": "text_message"},
            ]
        ),
        status="live",
        title="Example",
        id="abc",
    )
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        max_messages=1,
        output=str(output),
    )
    site = _FakeSite()

    configure_chat(chat, request, cast("Any", site))

    assert chat.site is site
    assert list(chat) == [{"message_id": "one", "message_type": "text_message"}]
    assert '"message_id": "one"' in output.read_text(encoding="utf-8")
    assert "two" not in output.read_text(encoding="utf-8")

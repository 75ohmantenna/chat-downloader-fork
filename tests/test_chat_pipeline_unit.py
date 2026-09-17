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


@pytest.mark.parametrize("interrupted", [False, True])
def test_message_limit_closes_source_once(interrupted):
    source = MagicMock()
    source.__next__.side_effect = KeyboardInterrupt if interrupted else [{"id": "1"}]
    chat = Chat(source)
    apply_message_limit(chat, 1)
    limited = cast("Any", chat.chat)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            next(limited)
    else:
        assert next(limited) == {"id": "1"}
        chat.close()
        limited.close()
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
    def __init__(self, live=False):
        self.live = live

    def is_live_status(self, status):
        return self.live and status == "live"

    def resolve_live_format(self, format_name):
        return {"default": "live_default", "custom": "live_custom"}.get(
            format_name, format_name
        )


@pytest.mark.parametrize(
    ("status", "format_name", "has_site", "expected"),
    [
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
        chat.site = cast("Any", _FakeSite(live=True))
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


@pytest.mark.parametrize(
    ("suffix", "template", "existing"),
    [
        ("txt", False, True),
        ("jsonl", True, True),
        ("jsonl", False, False),
    ],
)
def test_output_aliases_write_each_item_once(tmp_path, suffix, template, existing):
    output = tmp_path / f"same.{suffix}"
    old = "existing\n" if suffix == "txt" else '{"old": true}\n'
    if existing:
        output.write_text(old, encoding="utf-8")
    alias = (
        str(tmp_path / "{title}.jsonl") if template else f"{tmp_path}/./same.{suffix}"
    )
    request = ChatRequest(output=[alias, str(output)], overwrite=False)
    message = {"message_type": "text_message", "message": "hello"}
    chat = Chat(iter([message]), title="same", status="live")
    chat.set_formatter(lambda _: "hello")
    configure_output_writer(chat, request)
    assert list(chat) == [message]
    lines = output.read_text(encoding="utf-8").splitlines()
    if existing:
        assert lines.pop(0) == old.strip()
    assert len(lines) == 1
    assert (lines[0] if suffix == "txt" else json.loads(lines[0])) == (
        "hello" if suffix == "txt" else message
    )


def test_configure_output_writer_rejects_json_output(tmp_path):
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        configure_output_writer(
            Chat(status="live"), ChatRequest(output=str(tmp_path / "out.json"))
        )


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

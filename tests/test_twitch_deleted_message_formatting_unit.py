# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item


def test_clearmsg_parses_and_formats_as_a_deletion() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures/twitch/live_events/irc-clearmsg-deleted-message.json"
    )
    raw = json.loads(fixture.read_text(encoding="utf-8"))["raw"]
    match = MESSAGE_REGEX.search(raw)
    assert match is not None
    parsed = _parse_irc_item(match)

    assert parsed["message_type"] == "delete_message"
    assert parsed["target_message_id"] == "deleted-message-1"
    assert "message_id" not in parsed
    assert ItemFormatter().format(parsed, "twitch") == (
        "2023-11-14 22:13:20 | [DELETED] deleteduser"
        " — Removed chat text [message deleted-message-1]"
    )


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "[DELETED]"),
        ({"target_message_id": "target"}, "[DELETED] [message target]"),
        ({"message": "removed"}, "[DELETED] — removed"),
        ({"message": "", "author": {}}, "[DELETED]"),
        (
            {"author": None, "message": None, "target_message_id": None},
            "[DELETED]",
        ),
        ({"author": {"name": "login"}}, "[DELETED] login"),
        (
            {"author": {"display_name": "Display", "name": "login"}},
            "[DELETED] Display",
        ),
        (
            {"author": {"display_name": "", "name": "login"}},
            "[DELETED] login",
        ),
        ({"time_text": "0:42"}, "0:42 | [DELETED]"),
        ({"timestamp": 0}, "1970-01-01 00:00:00 | [DELETED]"),
        (
            {"time_text": "", "timestamp": 0},
            "1970-01-01 00:00:00 | [DELETED]",
        ),
        (
            {"message": "{target_message_id} 🐭"},
            "[DELETED] — {target_message_id} 🐭",
        ),
    ],
)
def test_deletion_format_handles_partial_records(fields, expected) -> None:
    record = {"message_type": "delete_message", **fields}
    original = json.dumps(record)
    assert ItemFormatter().format(record, "twitch") == expected
    assert json.dumps(record) == original


def test_deleted_text_cannot_forge_output_lines(tmp_path) -> None:
    record = {
        "message_type": "delete_message",
        "author": {"name": "user\r\n[FAKE]"},
        "message": "removed\n[FAKE]\u2028🐭\x1b",
        "target_message_id": "target\n[FAKE]",
    }
    formatter = ItemFormatter()
    jsonl = tmp_path / "chat.jsonl"
    txt = tmp_path / "chat.txt"
    chat = Chat(chat=iter([record]))
    chat.set_formatter(lambda item: formatter.format(item, "twitch"))
    chat.attach_writer(ContinuousWriter(str(jsonl), lazy_initialise=True))
    chat.attach_writer(ContinuousWriter(str(txt), lazy_initialise=True))
    try:
        assert list(chat) == [record]
    finally:
        chat.close()

    assert json.loads(jsonl.read_text(encoding="utf-8")) == record
    assert txt.read_text(encoding="utf-8") == (
        r"[DELETED] user\r\n[FAKE] — removed\n[FAKE]\u2028🐭"
        r" [message target\n[FAKE]]" + "\n"
    )
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="twitch")
    assert not stats.failed
    assert stats.txt_lines == 1


def test_deletion_does_not_replace_or_suppress_the_original_message(tmp_path) -> None:
    original = {
        "message_type": "text_message",
        "message_id": "target",
        "author": {"name": "user"},
        "message": "original text",
    }
    deletion = {
        "message_type": "delete_message",
        "target_message_id": "target",
        "author": {"name": "user"},
        "message": "original text",
    }
    records = [original, deletion]
    formatter = ItemFormatter()
    jsonl = tmp_path / "chat.jsonl"
    txt = tmp_path / "chat.txt"
    chat = Chat(chat=iter(records))
    chat.set_formatter(lambda item: formatter.format(item, "twitch"))
    chat.attach_writer(ContinuousWriter(str(jsonl), lazy_initialise=True))
    chat.attach_writer(ContinuousWriter(str(txt), lazy_initialise=True))
    try:
        assert list(chat) == records
    finally:
        chat.close()

    assert [json.loads(line) for line in jsonl.read_text().splitlines()] == records
    assert txt.read_text(encoding="utf-8").splitlines() == [
        "user: original text",
        "[DELETED] user — original text [message target]",
    ]
    assert not audit_capture(
        jsonl, txt, formatter=formatter, format_name="twitch"
    ).failed


def test_custom_twitch_deletion_format_still_overrides_the_builtin(tmp_path) -> None:
    path = tmp_path / "format.json"
    path.write_text(
        json.dumps({"twitch": {"template": "Removed {target_message_id}"}}),
        encoding="utf-8",
    )
    formatter = ItemFormatter(str(path))
    assert (
        formatter.format(
            {"message_type": "delete_message", "target_message_id": "target"}, "twitch"
        )
        == "Removed target"
    )

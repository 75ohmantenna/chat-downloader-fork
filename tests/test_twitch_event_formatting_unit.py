# SPDX-License-Identifier: MIT

"""Opt-in Twitch labels composed with parsing and both output writers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.twitch.constants import MESSAGE_REGEX
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item
from chat_downloader.sites.twitch.types import BadgeSet

FIXTURES = Path(__file__).parent / "fixtures/twitch/live_events"


def parse_fixture(name, *, same_channel=False):
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["raw"]
    if same_channel:
        raw = raw.replace("source-room-id=456", "source-room-id=123")
    match = MESSAGE_REGEX.search(raw)
    assert match is not None
    item = _parse_irc_item(match, BadgeSet(global_badges={}, channel_badges={}))
    item["time_text"] = "0:00"
    return item


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            "irc-privmsg-paid-pinned-chat.json",
            (
                "[PAID PINNED amount=1250] [amount exponent=2] [currency=USD] "
                "PaidUser: Pinned hello"
            ),
        ),
        (
            "irc-privmsg-shared-chat.json",
            "[SHARED CHAT] [source channel=456] GuestUser: hello",
        ),
        (
            "irc-usernotice-announcement.json",
            "[ANNOUNCEMENT] StreamElements: DinkDonk GAMBA",
        ),
        ("irc-privmsg-animated-message.json", "[ANIMATED] AnimatedUser: hello"),
    ],
)
def test_event_labels_use_parsed_metadata_without_changing_records(fixture, expected):
    item = parse_fixture(fixture)
    before = json.dumps(item)
    assert ItemFormatter().format(item, "twitch_events") == "0:00 | " + expected
    assert json.dumps(item) == before


def test_shared_chat_same_channel_uses_source_attribution():
    item = parse_fixture("irc-privmsg-shared-chat.json", same_channel=True)
    assert item["shared_chat_is_cross_channel"] is False
    assert ItemFormatter().format(item, "twitch_events") == (
        "0:00 | [SHARED CHAT] [source channel=123] GuestUser: hello"
    )


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, ""),
        ({"is_shared_chat_message": False}, ""),
        ({"is_shared_chat_message": True}, "[SHARED CHAT] "),
        ({"pinned_chat_paid_amount": 0}, "[PAID PINNED amount=0] "),
        ({"pinned_chat_paid_exponent": 0}, "[amount exponent=0] "),
        ({"pinned_chat_paid_currency": "JPY"}, "[currency=JPY] "),
        ({"pinned_chat_paid_amount": 10**30}, f"[PAID PINNED amount={10**30}] "),
        ({"pinned_chat_paid_amount": None, "pinned_chat_paid_currency": ""}, ""),
    ],
)
def test_partial_labels_preserve_zero_and_exact_integer_amounts(fields, expected):
    item = {
        "message_type": "text_message",
        "author": {"name": "user"},
        "message": "hello",
        **fields,
    }
    assert ItemFormatter().format(item, "twitch_events") == expected + "user: hello"


@pytest.mark.parametrize("message_type", ["announcement", "animated-message"])
def test_empty_special_messages_keep_their_label(message_type):
    label = "[ANNOUNCEMENT] " if message_type == "announcement" else "[ANIMATED] "
    assert (
        ItemFormatter().format({"message_type": message_type}, "twitch_events") == label
    )


def test_event_format_preserves_other_events_and_preset_definitions():
    formatter = ItemFormatter()
    before = json.dumps(formatter.format_file)
    for fixture in sorted(FIXTURES.glob("*.json")):
        item = parse_fixture(fixture.name)
        if item["message_type"] not in {
            "text_message",
            "announcement",
            "animated-message",
        }:
            assert formatter.format(item, "twitch_events") == formatter.format(
                item, "twitch"
            )
    assert json.dumps(formatter.format_file) == before


def test_event_format_composes_with_jsonl_txt_and_control_character_safety(tmp_path):
    records = [parse_fixture(f.name) for f in sorted(FIXTURES.glob("*.json"))]
    records.append(
        {
            "message_type": "text_message",
            "message_id": "control",
            "pinned_chat_paid_currency": "USD\n[FAKE]\x1b",
            "is_shared_chat_message": True,
            "shared_chat_effective_source_channel_id": "123\u2028[FAKE]",
            "author": {"name": "user"},
            "message": "hello\r\nworld",
        }
    )
    original = json.dumps(records)
    formatter = ItemFormatter()
    jsonl = tmp_path / "chat.jsonl"
    txt = tmp_path / "chat.txt"
    chat = Chat(chat=iter(records))
    chat.set_formatter(lambda item: formatter.format(item, "twitch_events"))
    chat.attach_writer(ContinuousWriter(str(jsonl), lazy_initialise=True))
    chat.attach_writer(ContinuousWriter(str(txt), lazy_initialise=True))
    try:
        assert list(chat) == records
    finally:
        chat.close()
    assert json.dumps(records) == original
    assert [json.loads(line) for line in jsonl.read_text().splitlines()] == json.loads(
        original
    )
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="twitch_events")
    assert not stats.failed
    assert stats.txt_lines == len(records)
    assert txt.read_text().splitlines()[-1] == (
        r"[currency=USD\n[FAKE]] [SHARED CHAT] [source channel=123\u2028[FAKE]] "
        r"user: hello\r\nworld"
    )

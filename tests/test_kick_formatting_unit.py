# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.writers import TextContinuousWriter
from chat_downloader.sites.kick.parsing.events import dispatch_event
from chat_downloader.sites.kick.parsing.messages import parse_chat_message
from chat_downloader.sites.kick.parsing.moderation import parse_message_deleted_event
from chat_downloader.sites.kick.parsing.pins import parse_pinned_message_created_event
from tests.kick_helpers import load_fixture, raw_message


@pytest.fixture
def formatter() -> ItemFormatter:
    return ItemFormatter()


@pytest.mark.parametrize(
    ("kind", "text", "metadata", "expected"),
    [
        (
            "message_deleted",
            "",
            {"deleted_message_id": "deleted-id"},
            "[Message deleted: deleted-id]",
        ),
        (
            "user_banned",
            "",
            {"user": {"username": "BadUser"}},
            "[User banned: BadUser]",
        ),
        ("user_unbanned", "", {"user": {"id": "42"}}, "[User unbanned: 42]"),
        ("chat_clear", "", None, "[Chat cleared]"),
        (
            "pinned_message_deleted",
            "",
            {"unpinned_message_id": "pin-id"},
            "[Pinned message removed: pin-id]",
        ),
        ("poll_update", "Example poll", None, "[Poll update] Example poll"),
        ("poll_update", "", None, "[Poll update]"),
        ("poll_deleted", "", None, "[Poll deleted]"),
        (
            "message_deleted",
            "",
            {
                "deleted_message_id": "deleted-id",
                "ai_moderated": False,
                "violated_rules": [],
            },
            "[Message deleted: deleted-id]",
        ),
    ],
)
def test_kick_notices(formatter, kind, text, metadata, expected):
    item = {"message_type": kind, "message": text}
    if metadata is not None:
        item["metadata"] = metadata
    assert formatter.format(item, format_name="kick") == expected


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("pinned_message", "Pinned message"),
        ("subscription", "Subscription"),
        ("gifted_subscriptions", "Gifted subscriptions"),
        ("stream_host", "Stream host"),
    ],
)
@pytest.mark.parametrize("authored", [False, True])
def test_system_event_optional_author_and_content(formatter, kind, label, authored):
    item = {"message_type": kind, "message": "Details" if authored else ""}
    if authored:
        item["author"] = {"display_name": "Author"}
    suffix = " Author — Details" if authored else ""
    assert formatter.format(item, format_name="kick") == f"[{label}]{suffix}"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            {
                "author": {
                    "badges": [{"title": "Moderator"}, {"title": "Subscriber"}],
                    "display_name": "Author",
                },
                "message": "Details",
                "message_type": "pinned_message",
            },
            "[Pinned message] (Moderator, Subscriber) Author — Details",
        ),
        (
            {
                "author": {"display_name": "Author"},
                "message": "Hello",
                "message_type": "text_message",
                "timestamp": 1577836800000000,
            },
            "2020-01-01 00:00:00 | Author: Hello",
        ),
    ],
)
def test_badge_spacing_and_default_text_rendering(formatter, item, expected):
    assert formatter.format(item, format_name="kick") == expected


@pytest.mark.parametrize(
    ("parser", "fixture", "expected"),
    [
        (
            parse_message_deleted_event,
            "message_deleted_event_ai.json",
            (
                "[Message deleted: ai-deleted-message] [AI moderated] "
                "(rules: hate, harassment)"
            ),
        ),
        (
            parse_pinned_message_created_event,
            "pinned_message_created_event_current.json",
            "[Pinned message] (Subscriber) MessageAuthor — Current pin payload",
        ),
        (
            parse_chat_message,
            "reply_message_event_data.json",
            (
                "2026-08-25 09:29:20 | ReplyAuthor "
                "[replying to OriginalAuthor]: Reply text"
            ),
        ),
    ],
)
def test_provider_events_render_end_to_end(formatter, parser, fixture, expected):
    assert (
        formatter.format(parser(load_fixture(fixture)), format_name="kick") == expected
    )


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("badged", [False, True])
@pytest.mark.parametrize(
    ("months", "label"),
    [
        (20, "[Subscribed for 20 months] "),
        (1, "[Subscribed for 1 month] "),
        (0, ""),
        (None, ""),
        (-1, ""),
        (True, ""),
        ("invalid", ""),
    ],
)
def test_subscription_celebration_months_render_end_to_end(
    formatter, encoded, badged, months, label
):
    payload = load_fixture("celebration_message_event_data.json")
    if not badged:
        payload["sender"]["identity"]["badges"] = []
    celebration = payload["metadata"]["celebration"]
    if months is None:
        celebration.pop("total_months")
    else:
        celebration["total_months"] = months
    if encoded:
        payload["metadata"] = json.dumps(payload["metadata"])
    item = parse_chat_message(payload)
    badges = "(Subscriber) " if badged else ""
    assert formatter.format(item, format_name="kick") == (
        f"2026-08-29 01:47:39 | {label}{badges}RenewalUser: Celebrating 20 months!"
    )
    assert formatter.format(item, format_name="default") == (
        f"2026-08-29 01:47:39 | {badges}RenewalUser: Celebrating 20 months!"
    )


def test_subscription_celebration_without_timestamp_has_no_leading_space(formatter):
    payload = load_fixture("celebration_message_event_data.json")
    payload.pop("created_at")
    item = parse_chat_message(payload)
    assert formatter.format(item, format_name="kick") == (
        "[Subscribed for 20 months] (Subscriber) RenewalUser: Celebrating 20 months!"
    )


def test_modern_badge_without_title_does_not_render_empty_marker(formatter):
    item = parse_chat_message(
        raw_message(
            "v2-only",
            "2026-08-18T22:54:20Z",
            "modern badges",
            sender={
                "id": 101,
                "username": "BadgeUser",
                "identity": {"badges_v2": [{"name": "level", "selected": True}]},
            },
        )
    )
    assert formatter.format(item, format_name="kick") == (
        "2026-08-18 22:54:20 | BadgeUser: modern badges"
    )


def test_moderation_receive_timestamp_is_only_a_fallback(formatter):
    item = {
        "message_type": "user_banned",
        "message": "",
        "received_timestamp": 1_577_836_800_000_000,
        "metadata": {"user": {"username": "BadUser"}},
    }
    assert formatter.format(item, format_name="kick") == (
        "2020-01-01 00:00:00 [received] | [User banned: BadUser]"
    )
    item["timestamp"] = 1_577_923_200_000_000
    assert formatter.format(item, format_name="kick") == (
        "2020-01-02 00:00:00 | [User banned: BadUser]"
    )


def test_channel_metadata_renders_full_public_state(formatter):
    payload = load_fixture("channel_live.json")
    frame = {
        "event": "kick:public_state",
        "source": "public_rest",
        "data": payload,
    }
    item = dispatch_event(frame, received_timestamp=1_577_836_800_000_000)
    assert item is not None
    rendered = formatter.format(item, format_name="kick")
    prefix = "2020-01-01 00:00:00 [received] | [channel metadata] "
    assert rendered.startswith(prefix)
    assert json.loads(rendered.removeprefix(prefix)) == item["metadata"]
    assert "\n" not in rendered
    item["timestamp"] = 1_577_923_200_000_000
    assert formatter.format(item, format_name="kick").startswith(
        "2020-01-02 00:00:00 | [channel metadata] "
    )


def test_channel_metadata_without_metadata_keeps_notice(formatter):
    assert (
        formatter.format(
            {"message_type": "channel_metadata", "message": "channel metadata"}, "kick"
        )
        == "[channel metadata]"
    )


def test_channel_metadata_with_surrogates_writes_lossless_json(formatter, tmp_path):
    payload = {"livestream": {"session_title": "Live 🎙️\ud800title\udfff"}}
    item = dispatch_event(
        {"event": "kick:public_state", "source": "public_rest", "data": payload},
        received_timestamp=1_577_836_800_000_000,
    )
    assert item is not None
    path = tmp_path / "capture.txt"
    writer = TextContinuousWriter(str(path))
    try:
        writer.write(formatter.format(item, "kick"))
    finally:
        writer.close()
    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    assert "🎙️" in text
    metadata = text.split("[channel metadata] ", 1)[1]
    assert json.loads(metadata) == item["metadata"]


@pytest.mark.parametrize(
    ("data", "suffix"),
    [
        (
            {
                "sender": {"username": "GiftUser"},
                "gift": {"amount": 500, "name": "Rage Quit"},
                "message": "Great stream!",
            },
            " GiftUser — 500 Kicks (Rage Quit): Great stream!",
        ),
        (
            {
                "sender": {"username": "GiftUser"},
                "gift": {"amount": 500, "name": "Rage Quit"},
                "message": "",
            },
            " GiftUser — 500 Kicks (Rage Quit)",
        ),
        ({"gift": {"amount": 500}}, " — 500 Kicks"),
        ({}, ""),
        (
            {
                "sender": {"username": "GiftUser\nForged\x1b"},
                "gift": {"amount": 500, "name": "Rage\nQuit"},
                "message": "Hello\r\nWorld\x1b",
            },
            r" GiftUser\nForged — 500 Kicks (Rage\nQuit): Hello\r\nWorld",
        ),
    ],
)
def test_kicks_gifted_details_and_plain_receive_timestamp(formatter, data, suffix):
    item = {
        "message_type": "kicks_gifted",
        "message": "kicks gifted",
        "received_timestamp": 1_577_836_800_000_000,
        "metadata": {"data": data},
    }
    assert formatter.format(item, format_name="kick") == (
        f"2020-01-01 00:00:00 | [Kicks gifted]{suffix}"
    )
    item["timestamp"] = 1_577_923_200_000_000
    assert formatter.format(item, format_name="kick") == (
        f"2020-01-02 00:00:00 | [Kicks gifted]{suffix}"
    )


@pytest.mark.parametrize("encoded", [False, True])
def test_recorded_kicks_gifted_dispatch_and_formatting(formatter, encoded):
    fixture = load_fixture("kicks_gifted_event_recorded.json")
    frame = fixture["frame"]
    payload = frame["data"]
    if encoded:
        frame = {**frame, "data": json.dumps(payload)}

    item = dispatch_event(frame, received_timestamp=fixture["received_timestamp"])

    assert item is not None
    assert item["message_type"] == "kicks_gifted"
    assert item["message_id"] == "kick-kicks_gifted:1791067182588500"
    assert item["received_timestamp"] == 1_791_067_182_588_500
    assert "timestamp" not in item
    assert item["metadata"] == {
        "event_name": "KicksGifted",
        "channel": "channel_123",
        "data": payload,
        "source": "websocket",
    }
    assert formatter.format(item, format_name="kick") == (
        "2026-10-03 22:39:42 | [Kicks gifted] GiftUser"
        " — 500 Kicks (Rage Quit): Great stream!"
    )


@pytest.mark.parametrize(
    ("reply", "label"),
    [
        ({"author": {"display_name": "Parent", "name": "slug"}}, "replying to Parent"),
        ({"author": {"name": "slug"}}, "replying to slug"),
        ({"message_id": "parent-id"}, "reply to message parent-id"),
        ({"thread_parent_message_id": "thread-id"}, "reply to message thread-id"),
        ({}, None),
        ({"author": {"display_name": ""}}, None),
        (
            {"author": {"display_name": "Parent\nForged\x1b"}},
            r"replying to Parent\nForged",
        ),
    ],
)
def test_reply_context_fallbacks_and_safe_rendering(formatter, reply, label):
    item = {
        "message_type": "text_message",
        "author": {"display_name": "Author"},
        "message": "Hello",
        "in_reply_to": reply,
    }
    suffix = f" [{label}]" if label else ""
    assert formatter.format(item, format_name="kick") == f"Author{suffix}: Hello"
    assert formatter.format(item, format_name="default") == "Author: Hello"

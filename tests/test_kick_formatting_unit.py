# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.formatting.format import ItemFormatter
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

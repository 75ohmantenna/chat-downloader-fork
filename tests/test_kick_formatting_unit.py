# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.kick.parsing.moderation import (
    parse_message_deleted_event,
)
from chat_downloader.sites.kick.parsing.pins import (
    parse_pinned_message_created_event,
)
from tests.kick_helpers import load_fixture


@pytest.fixture
def formatter() -> ItemFormatter:
    return ItemFormatter()


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            {
                "message_type": "message_deleted",
                "message": "",
                "metadata": {"deleted_message_id": "deleted-id"},
            },
            "[Message deleted: deleted-id]",
        ),
        (
            {
                "message_type": "user_banned",
                "message": "",
                "metadata": {"user": {"username": "BadUser"}},
            },
            "[User banned: BadUser]",
        ),
        (
            {
                "message_type": "user_unbanned",
                "message": "",
                "metadata": {"user": {"id": "42"}},
            },
            "[User unbanned: 42]",
        ),
        (
            {"message_type": "chat_clear", "message": ""},
            "[Chat cleared]",
        ),
        (
            {
                "message_type": "pinned_message_deleted",
                "message": "",
                "metadata": {"unpinned_message_id": "pin-id"},
            },
            "[Pinned message removed: pin-id]",
        ),
    ],
)
def test_kick_moderation_and_unpin_notices_are_not_blank(
    formatter: ItemFormatter,
    item: dict[str, object],
    expected: str,
) -> None:
    assert formatter.format(item, format_name="kick") == expected


@pytest.mark.parametrize(
    ("message_type", "label"),
    [
        ("pinned_message", "Pinned message"),
        ("subscription", "Subscription"),
        ("gifted_subscriptions", "Gifted subscriptions"),
        ("stream_host", "Stream host"),
    ],
)
def test_kick_authored_system_events_have_labels_and_content(
    formatter: ItemFormatter,
    message_type: str,
    label: str,
) -> None:
    item = {
        "author": {"display_name": "Author"},
        "message": "Details",
        "message_type": message_type,
    }

    assert formatter.format(item, format_name="kick") == (f"[{label}] Author — Details")


@pytest.mark.parametrize(
    ("message_type", "label"),
    [
        ("pinned_message", "Pinned message"),
        ("subscription", "Subscription"),
        ("gifted_subscriptions", "Gifted subscriptions"),
        ("stream_host", "Stream host"),
    ],
)
def test_kick_system_event_without_optional_fields_is_not_blank(
    formatter: ItemFormatter,
    message_type: str,
    label: str,
) -> None:
    result = formatter.format(
        {"message": "", "message_type": message_type},
        format_name="kick",
    )

    assert result == f"[{label}]"


def test_kick_system_event_badges_have_clean_spacing(
    formatter: ItemFormatter,
) -> None:
    item = {
        "author": {
            "badges": [{"title": "Moderator"}, {"title": "Subscriber"}],
            "display_name": "Author",
        },
        "message": "Details",
        "message_type": "pinned_message",
    }

    assert formatter.format(item, format_name="kick") == (
        "[Pinned message] (Moderator, Subscriber) Author — Details"
    )


def test_kick_text_messages_keep_default_rendering(formatter: ItemFormatter) -> None:
    item = {
        "author": {"display_name": "Author"},
        "message": "Hello",
        "message_type": "text_message",
        "timestamp": 1577836800000000,
    }

    assert formatter.format(item, format_name="kick") == (
        "2020-01-01 00:00:00 | Author: Hello"
    )


def test_kick_moderation_uses_receive_timestamp_only_as_fallback(
    formatter: ItemFormatter,
) -> None:
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


def test_provider_shaped_system_events_render_end_to_end(
    formatter: ItemFormatter,
) -> None:
    deleted = parse_message_deleted_event(load_fixture("message_deleted_event_ai.json"))
    pinned = parse_pinned_message_created_event(
        load_fixture("pinned_message_created_event_current.json")
    )

    assert formatter.format(deleted, format_name="kick") == (
        "[Message deleted: ai-deleted-message]"
    )
    assert formatter.format(pinned, format_name="kick") == (
        "[Pinned message] (Subscriber) MessageAuthor — Current pin payload"
    )

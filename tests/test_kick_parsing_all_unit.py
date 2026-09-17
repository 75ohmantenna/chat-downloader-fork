# SPDX-License-Identifier: MIT

"""Unit tests for Kick event-type parser functions.

Tests every event parser from subscriptions, moderation, pins, and hosts
modules: valid parsing, None/empty/missing-field edge cases.
"""

from __future__ import annotations

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.hosts import parse_stream_host_event
from chat_downloader.sites.kick.parsing.moderation import (
    parse_chat_clear_event,
    parse_message_deleted_event,
    parse_user_banned_event,
    parse_user_unbanned_event,
)
from chat_downloader.sites.kick.parsing.pins import (
    parse_pinned_message_created_event,
    parse_pinned_message_deleted_event,
)
from chat_downloader.sites.kick.parsing.subscriptions import (
    parse_gifted_subscriptions_event,
    parse_subscription_event,
)
from tests.kick_helpers import load_fixture


@pytest.fixture(
    params=[
        parse_subscription_event,
        parse_gifted_subscriptions_event,
        parse_user_banned_event,
        parse_user_unbanned_event,
        parse_message_deleted_event,
        parse_chat_clear_event,
        parse_pinned_message_created_event,
        parse_pinned_message_deleted_event,
        parse_stream_host_event,
    ]
)
def parser(request):
    return request.param


@pytest.mark.parametrize("payload", [None, {}, 3.14, "not a dict", 42, [], ""])
def test_event_parsers_reject_invalid_payloads(parser, payload: object) -> None:
    with pytest.raises(ParsingError):
        parser(payload)


@pytest.mark.parametrize("created_at", ["", "not-a-date", 123])
def test_event_parsers_omit_invalid_timestamps(parser, created_at: object) -> None:
    assert "timestamp" not in parser({"id": "x", "created_at": created_at})


def test_event_parsers_missing_optional_fields(parser) -> None:
    message_type = parser.__name__.removeprefix("parse_").removesuffix("_event")
    if message_type == "pinned_message_created":
        message_type = "pinned_message"
    msg = parser({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == message_type
    assert msg["message"] == ""
    assert "timestamp" not in msg
    assert "author" not in msg
    assert "metadata" not in msg


@pytest.mark.parametrize(
    ("parser", "raw_id", "expected"),
    [(parse_user_banned_event, 777, "777"), (parse_user_unbanned_event, 42, "42")],
)
def test_numeric_id_coercion(parser, raw_id, expected) -> None:
    assert parser({"id": raw_id})["message_id"] == expected


@pytest.mark.parametrize(
    ("parser", "kind", "message_id", "text", "author", "metadata"),
    [
        (
            parse_subscription_event,
            "subscription",
            "sub-001-abc-def",
            "cooluser95 subscribed!",
            {
                "id": "1001",
                "display_name": "cooluser95",
                "name": "cooluser95",
                "colour": "#FF69B4",
            },
            {"months": 3, "plan": "primary_1", "gift": False},
        ),
        (
            parse_gifted_subscriptions_event,
            "gifted_subscriptions",
            "gift-002-ghi-jkl",
            "richgifter99 gifted 5 subscriptions!",
            {"id": "2002", "display_name": "richgifter99"},
            {
                "quantity": 5,
                "plan": "primary_1",
                "gifter_username": "richgifter99",
                "recipients": ["user_a", "user_b", "user_c", "user_d", "user_e"],
                "gift": True,
            },
        ),
        (
            parse_user_banned_event,
            "user_banned",
            "ban-003-mno-pqr",
            "",
            {},
            {
                "user": {"id": "3003", "username": "toxic_troller"},
                "banned_by": {"id": "1", "username": "streamer_chan"},
            },
        ),
        (
            parse_user_unbanned_event,
            "user_unbanned",
            "unban-004-stu-vwx",
            "",
            {},
            {
                "user": {"id": "3003", "username": "toxic_troller"},
                "unbanned_by": {"id": "1", "username": "streamer_chan"},
            },
        ),
        (
            parse_message_deleted_event,
            "message_deleted",
            "del-005-yza-bcd",
            "",
            {},
            {"deleted_message_id": "original-msg-999"},
        ),
        (
            parse_chat_clear_event,
            "chat_clear",
            "clear-009-wxy-zab",
            "",
            {},
            {"chatroom_id": 12345},
        ),
        (
            parse_pinned_message_deleted_event,
            "pinned_message_deleted",
            "unpin-007-klm-nop",
            "",
            {},
            {"unpinned_message_id": "pinned-msg-001"},
        ),
        (
            parse_stream_host_event,
            "stream_host",
            "host-008-qrs-tuv",
            "Come check out my stream!",
            {"id": "4004", "display_name": "hosting_user"},
            {
                "host_username": "hosting_user",
                "number_viewers": 150,
                "optional_message": "Come check out my stream!",
            },
        ),
    ],
)
def test_standard_event_fixtures(parser, kind, message_id, text, author, metadata):
    msg = parser(load_fixture(f"{kind}_event.json"))
    assert msg["message_id"] == message_id
    assert msg["message_type"] == kind
    assert msg["message"] == text
    assert isinstance(msg["timestamp"], int)
    for key, value in author.items():
        assert msg["author"][key] == value
    assert msg["metadata"] == metadata
    if "gift" in metadata:
        assert msg["metadata"]["gift"] is metadata["gift"]


def test_parse_current_temporary_user_ban_metadata() -> None:
    raw = load_fixture("user_banned_event_temporary.json")

    msg = parse_user_banned_event(raw)

    assert msg["metadata"] == {
        "user": {"id": "2002", "username": "ExampleUser"},
        "banned_by": {"id": "1001", "username": "ExampleModerator"},
        "expires_at": 1788131797000000,
        "duration": 5,
        "permanent": False,
    }


def test_parse_ai_moderated_message_deleted_event() -> None:
    raw = load_fixture("message_deleted_event_ai.json")

    msg = parse_message_deleted_event(raw)

    assert msg["metadata"] == {
        "deleted_message_id": "ai-deleted-message",
        "ai_moderated": True,
        "violated_rules": ["hate", "harassment"],
    }


def test_message_deleted_preserves_false_ai_flag_and_filters_rules() -> None:
    msg = parse_message_deleted_event(
        {
            "id": "x",
            "aiModerated": False,
            "violatedRules": ["valid", 7, None],
        }
    )

    assert msg["metadata"] == {
        "ai_moderated": False,
        "violated_rules": ["valid"],
    }


def test_message_deleted_ignores_non_list_violated_rules() -> None:
    msg = parse_message_deleted_event({"id": "x", "violatedRules": "hate"})

    assert "metadata" not in msg


@pytest.mark.parametrize(
    (
        "fixture",
        "message_id",
        "text",
        "author",
        "pinned_id",
        "created",
        "duration",
        "timestamp",
    ),
    [
        (
            "pinned_message_created_event.json",
            "pin-006-efg-hij",
            "Welcome to the stream! Read the rules!",
            "streamer_chan",
            "pinned-msg-001",
            1749902400000000,
            120,
            1749903900000000,
        ),
        (
            "pinned_message_created_event_current.json",
            "kick-pin:current-pinned-message",
            "Current pin payload",
            "MessageAuthor",
            "current-pinned-message",
            1787650147000000,
            1200,
            None,
        ),
    ],
)
def test_pinned_message_fixtures(
    fixture, message_id, text, author, pinned_id, created, duration, timestamp
):
    msg = parse_pinned_message_created_event(load_fixture(fixture))
    assert msg["message_id"] == message_id
    assert msg["message_type"] == "pinned_message"
    assert msg["message"] == text
    assert msg["author"]["display_name"] == author
    assert msg["metadata"]["pinned_message_id"] == pinned_id
    assert msg["metadata"]["original_message_created_at"] == created
    assert msg["metadata"]["pinned_message_created_at"] == created
    assert msg["metadata"]["duration"] == duration
    if timestamp is None:
        assert "timestamp" not in msg
        assert msg["metadata"]["pinned_by"]["display_name"] == "PinningModerator"
    else:
        assert isinstance(msg["timestamp"], int)
        assert msg["timestamp"] == timestamp
        assert "pinned_by" not in msg["metadata"]


def test_pinned_message_created_rejects_empty_nested_id() -> None:
    with pytest.raises(ParsingError):
        parse_pinned_message_created_event({"message": {"id": ""}})


def test_pinned_message_deleted_uses_namespaced_nested_id_fallback() -> None:
    msg = parse_pinned_message_deleted_event({"message": {"id": "nested"}})

    assert msg["message_id"] == "kick-unpin:nested"
    assert msg["metadata"]["unpinned_message_id"] == "nested"


@pytest.mark.parametrize(
    ("expiry", "metadata"),
    [
        ("2024-01-01T00:01:00Z", {"expires_at": 1704067260000000}),
        ("", {"expires_at": ""}),
    ],
)
def test_user_banned_string_expiry(expiry, metadata) -> None:
    assert (
        parse_user_banned_event({"id": "x", "expires_at": expiry})["metadata"]
        == metadata
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration", True),
        ("duration", -1),
        ("permanent", 1),
        ("expires_at", "not-a-date"),
    ],
)
def test_user_banned_omits_invalid_timeout_metadata(
    field: str,
    value: object,
) -> None:
    msg = parse_user_banned_event({"id": "x", field: value})

    assert "metadata" not in msg


def test_pinned_message_omits_invalid_nested_timestamp() -> None:
    msg = parse_pinned_message_created_event(
        {"id": "x", "message": {"id": "pinned", "created_at": "not-a-date"}}
    )
    assert "original_message_created_at" not in msg["metadata"]
    assert "pinned_message_created_at" not in msg["metadata"]

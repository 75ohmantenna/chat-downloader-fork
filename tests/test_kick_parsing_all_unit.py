# SPDX-License-Identifier: MIT

"""Unit tests for Kick event-type parser functions.

Tests every event parser from subscriptions, moderation, pins, and hosts
modules: valid parsing, None/empty/missing-field edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    "parser",
    [
        pytest.param(parse_message_deleted_event, id="message-deleted"),
        pytest.param(parse_pinned_message_created_event, id="pin-created"),
        pytest.param(parse_pinned_message_deleted_event, id="pin-deleted"),
        pytest.param(parse_stream_host_event, id="stream-host"),
    ],
)
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="none"),
        pytest.param(3.14, id="non-object"),
    ],
)
def test_event_parsers_reject_non_object_payloads(
    parser: Callable[[object], dict[str, Any]],
    payload: object,
) -> None:
    with pytest.raises(ParsingError):
        parser(payload)


@pytest.mark.parametrize(
    "parser",
    [
        parse_subscription_event,
        parse_gifted_subscriptions_event,
        parse_user_banned_event,
        parse_user_unbanned_event,
        parse_message_deleted_event,
        parse_chat_clear_event,
        parse_pinned_message_created_event,
        parse_pinned_message_deleted_event,
        parse_stream_host_event,
    ],
)
@pytest.mark.parametrize("created_at", ["", "not-a-date", 123])
def test_event_parsers_omit_invalid_timestamps(
    parser: Callable[[object], dict[str, Any]],
    created_at: object,
) -> None:
    message = parser({"id": "x", "created_at": created_at})
    assert "timestamp" not in message


# ── subscription ───────────────────────────────────────────────────────


def test_parse_subscription_event() -> None:
    raw = load_fixture("subscription_event.json")
    msg = parse_subscription_event(raw)
    assert msg["message_id"] == "sub-001-abc-def"
    assert msg["message_type"] == "subscription"
    assert msg["message"] == "cooluser95 subscribed!"
    assert isinstance(msg["timestamp"], int)
    assert msg["author"]["id"] == "1001"
    assert msg["author"]["display_name"] == "cooluser95"
    assert msg["author"]["name"] == "cooluser95"
    assert msg["author"]["colour"] == "#FF69B4"
    assert msg["metadata"]["months"] == 3
    assert msg["metadata"]["plan"] == "primary_1"
    assert msg["metadata"]["gift"] is False


def test_subscription_none_raises() -> None:
    with pytest.raises(ParsingError):
        parse_subscription_event(None)


def test_subscription_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_subscription_event({})


def test_subscription_missing_optional_fields() -> None:
    msg = parse_subscription_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "subscription"
    assert msg["message"] == ""
    assert "timestamp" not in msg
    assert "author" not in msg
    assert "metadata" not in msg


def test_subscription_non_dict_payload_raises() -> None:
    with pytest.raises(ParsingError):
        parse_subscription_event("not a dict")


# ── gifted_subscriptions ───────────────────────────────────────────────


def test_parse_gifted_subscriptions_event() -> None:
    raw = load_fixture("gifted_subscriptions_event.json")
    msg = parse_gifted_subscriptions_event(raw)
    assert msg["message_id"] == "gift-002-ghi-jkl"
    assert msg["message_type"] == "gifted_subscriptions"
    assert msg["message"] == "richgifter99 gifted 5 subscriptions!"
    assert isinstance(msg["timestamp"], int)
    assert msg["author"]["id"] == "2002"
    assert msg["author"]["display_name"] == "richgifter99"
    assert msg["metadata"]["quantity"] == 5
    assert msg["metadata"]["plan"] == "primary_1"
    assert msg["metadata"]["gifter_username"] == "richgifter99"
    assert msg["metadata"]["recipients"] == [
        "user_a",
        "user_b",
        "user_c",
        "user_d",
        "user_e",
    ]
    assert msg["metadata"]["gift"] is True


def test_gifted_subscriptions_none_raises() -> None:
    with pytest.raises(ParsingError):
        parse_gifted_subscriptions_event(None)


def test_gifted_subscriptions_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_gifted_subscriptions_event({})


def test_gifted_subscriptions_missing_optional_fields() -> None:
    msg = parse_gifted_subscriptions_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "gifted_subscriptions"
    assert msg["message"] == ""
    assert "timestamp" not in msg
    assert "author" not in msg
    assert "metadata" not in msg


def test_gifted_subscriptions_non_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_gifted_subscriptions_event(42)


# ── user_banned ────────────────────────────────────────────────────────


def test_parse_user_banned_event() -> None:
    raw = load_fixture("user_banned_event.json")
    msg = parse_user_banned_event(raw)
    assert msg["message_id"] == "ban-003-mno-pqr"
    assert msg["message_type"] == "user_banned"
    assert msg["message"] == ""
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["user"]["id"] == "3003"
    assert msg["metadata"]["user"]["username"] == "toxic_troller"
    assert msg["metadata"]["banned_by"]["id"] == "1"
    assert msg["metadata"]["banned_by"]["username"] == "streamer_chan"
    # expires_at is null in fixture → not present in metadata
    assert "expires_at" not in msg["metadata"]


def test_user_banned_none_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_banned_event(None)


def test_user_banned_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_banned_event({})


def test_user_banned_missing_optional_fields() -> None:
    msg = parse_user_banned_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "user_banned"
    assert "timestamp" not in msg
    assert "metadata" not in msg


def test_user_banned_non_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_banned_event([])


# ── user_unbanned ──────────────────────────────────────────────────────


def test_parse_user_unbanned_event() -> None:
    raw = load_fixture("user_unbanned_event.json")
    msg = parse_user_unbanned_event(raw)
    assert msg["message_id"] == "unban-004-stu-vwx"
    assert msg["message_type"] == "user_unbanned"
    assert msg["message"] == ""
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["user"]["id"] == "3003"
    assert msg["metadata"]["user"]["username"] == "toxic_troller"
    assert msg["metadata"]["unbanned_by"]["id"] == "1"
    assert msg["metadata"]["unbanned_by"]["username"] == "streamer_chan"


def test_user_unbanned_none_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_unbanned_event(None)


def test_user_unbanned_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_unbanned_event({})


def test_user_unbanned_missing_optional_fields() -> None:
    msg = parse_user_unbanned_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert "timestamp" not in msg
    assert "metadata" not in msg


def test_user_unbanned_non_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_user_unbanned_event(3.14)


# ── message_deleted ────────────────────────────────────────────────────


def test_parse_message_deleted_event() -> None:
    raw = load_fixture("message_deleted_event.json")
    msg = parse_message_deleted_event(raw)
    assert msg["message_id"] == "del-005-yza-bcd"
    assert msg["message_type"] == "message_deleted"
    assert msg["message"] == ""
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["deleted_message_id"] == "original-msg-999"


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


def test_message_deleted_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_message_deleted_event({})


def test_message_deleted_missing_optional_fields() -> None:
    msg = parse_message_deleted_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "message_deleted"
    assert "timestamp" not in msg
    assert "metadata" not in msg


# ── chat_clear ─────────────────────────────────────────────────────────


def test_parse_chat_clear_event() -> None:
    raw = load_fixture("chat_clear_event.json")
    msg = parse_chat_clear_event(raw)
    assert msg["message_id"] == "clear-009-wxy-zab"
    assert msg["message_type"] == "chat_clear"
    assert msg["message"] == ""
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["chatroom_id"] == 12345


def test_chat_clear_none_raises() -> None:
    with pytest.raises(ParsingError):
        parse_chat_clear_event(None)


def test_chat_clear_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_chat_clear_event({})


def test_chat_clear_missing_optional_fields() -> None:
    msg = parse_chat_clear_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "chat_clear"
    assert "timestamp" not in msg
    assert "metadata" not in msg


def test_chat_clear_non_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_chat_clear_event("")


# ── pinned_message_created ─────────────────────────────────────────────


def test_parse_pinned_message_created_event() -> None:
    raw = load_fixture("pinned_message_created_event.json")
    msg = parse_pinned_message_created_event(raw)
    assert msg["message_id"] == "pin-006-efg-hij"
    assert msg["message_type"] == "pinned_message"
    assert msg["message"] == "Welcome to the stream! Read the rules!"
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["pinned_message_id"] == "pinned-msg-001"
    assert msg["author"]["display_name"] == "streamer_chan"
    assert "pinned_by" not in msg["metadata"]
    original_message_created_at = msg["metadata"]["original_message_created_at"]
    assert original_message_created_at == 1749902400000000
    assert msg["metadata"]["pinned_message_created_at"] == original_message_created_at
    assert msg["timestamp"] == 1749903900000000
    assert msg["metadata"]["duration"] == 120


def test_parse_current_pinned_message_created_event() -> None:
    raw = load_fixture("pinned_message_created_event_current.json")

    msg = parse_pinned_message_created_event(raw)

    assert msg["message_id"] == "kick-pin:current-pinned-message"
    assert msg["message"] == "Current pin payload"
    assert msg["author"]["display_name"] == "MessageAuthor"
    assert msg["metadata"]["pinned_message_id"] == "current-pinned-message"
    assert msg["metadata"]["pinned_by"]["display_name"] == "PinningModerator"
    original_message_created_at = msg["metadata"]["original_message_created_at"]
    assert original_message_created_at == 1787650147000000
    assert msg["metadata"]["pinned_message_created_at"] == original_message_created_at
    assert msg["metadata"]["duration"] == 1200
    assert "timestamp" not in msg


def test_pinned_message_created_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_pinned_message_created_event({})


def test_pinned_message_created_rejects_empty_nested_id() -> None:
    with pytest.raises(ParsingError):
        parse_pinned_message_created_event({"message": {"id": ""}})


def test_pinned_message_created_missing_optional_fields() -> None:
    msg = parse_pinned_message_created_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "pinned_message"
    assert msg["message"] == ""
    assert "timestamp" not in msg
    assert "metadata" not in msg


# ── pinned_message_deleted ─────────────────────────────────────────────


def test_parse_pinned_message_deleted_event() -> None:
    raw = load_fixture("pinned_message_deleted_event.json")
    msg = parse_pinned_message_deleted_event(raw)
    assert msg["message_id"] == "unpin-007-klm-nop"
    assert msg["message_type"] == "pinned_message_deleted"
    assert msg["message"] == ""
    assert isinstance(msg["timestamp"], int)
    assert msg["metadata"]["unpinned_message_id"] == "pinned-msg-001"


def test_pinned_message_deleted_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_pinned_message_deleted_event({})


def test_pinned_message_deleted_missing_optional_fields() -> None:
    msg = parse_pinned_message_deleted_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "pinned_message_deleted"
    assert "timestamp" not in msg
    assert "metadata" not in msg


def test_pinned_message_deleted_uses_namespaced_nested_id_fallback() -> None:
    msg = parse_pinned_message_deleted_event({"message": {"id": "nested"}})

    assert msg["message_id"] == "kick-unpin:nested"
    assert msg["metadata"]["unpinned_message_id"] == "nested"


# ── stream_host ────────────────────────────────────────────────────────


def test_parse_stream_host_event() -> None:
    raw = load_fixture("stream_host_event.json")
    msg = parse_stream_host_event(raw)
    assert msg["message_id"] == "host-008-qrs-tuv"
    assert msg["message_type"] == "stream_host"
    assert msg["message"] == "Come check out my stream!"
    assert isinstance(msg["timestamp"], int)
    assert msg["author"]["id"] == "4004"
    assert msg["author"]["display_name"] == "hosting_user"
    assert msg["metadata"]["host_username"] == "hosting_user"
    assert msg["metadata"]["number_viewers"] == 150
    assert msg["metadata"]["optional_message"] == "Come check out my stream!"


def test_stream_host_empty_dict_raises() -> None:
    with pytest.raises(ParsingError):
        parse_stream_host_event({})


def test_stream_host_missing_optional_fields() -> None:
    msg = parse_stream_host_event({"id": "x"})
    assert msg["message_id"] == "x"
    assert msg["message_type"] == "stream_host"
    assert msg["message"] == ""
    assert "timestamp" not in msg
    assert "author" not in msg
    assert "metadata" not in msg


# Numeric-ID coercion regression.
# Moderation events can have numeric top-level ids; _opt_str must stringify them.


def test_user_banned_numeric_id_coerced() -> None:
    """parse_user_banned_event accepts a numeric id and stringifies it."""
    msg = parse_user_banned_event({"id": 777})
    assert msg["message_id"] == "777"


def test_user_banned_parses_string_expiry() -> None:
    msg = parse_user_banned_event({"id": "x", "expires_at": "2024-01-01T00:01:00Z"})
    assert msg["metadata"]["expires_at"] == 1704067260000000


def test_user_banned_omits_invalid_string_expiry() -> None:
    msg = parse_user_banned_event({"id": "x", "expires_at": "not-a-date"})
    assert "metadata" not in msg


def test_user_banned_preserves_empty_string_expiry() -> None:
    msg = parse_user_banned_event({"id": "x", "expires_at": ""})
    assert msg["metadata"]["expires_at"] == ""


def test_pinned_message_omits_invalid_nested_timestamp() -> None:
    msg = parse_pinned_message_created_event(
        {"id": "x", "message": {"id": "pinned", "created_at": "not-a-date"}}
    )
    assert "original_message_created_at" not in msg["metadata"]
    assert "pinned_message_created_at" not in msg["metadata"]


def test_user_unbanned_numeric_id_coerced() -> None:
    """parse_user_unbanned_event accepts a numeric id and stringifies it."""
    msg = parse_user_unbanned_event({"id": 42})
    assert msg["message_id"] == "42"

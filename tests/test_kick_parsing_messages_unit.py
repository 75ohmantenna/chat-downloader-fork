# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.messages import (
    parse_chat_message,
    parse_preloaded_messages,
)
from tests.kick_helpers import load_fixture


def test_parse_full_chat_message() -> None:
    raw = load_fixture("chat_message_event_data.json")
    msg = parse_chat_message(raw)
    assert msg["message_id"] == "live-1"
    assert msg["message_type"] == "text_message"
    assert msg["message"] == "hello world PogU"
    assert msg["timestamp"] == 1704067260000000
    author = msg["author"]
    assert author["id"] == "99"
    assert author["display_name"] == "LiveUser"
    assert author["name"] == "liveuser"
    assert author["colour"] == "#FF0000"
    assert author["badges"] == [
        {"name": "moderator", "title": "Moderator"},
        {"name": "subscriber", "title": "Subscriber", "count": 5},
    ]
    assert msg["emotes"][0]["id"] == "37233"


def test_reply_type_maps_to_text_message() -> None:
    msg = parse_chat_message({"id": "x", "type": "reply", "content": "hi"})
    assert msg["message_type"] == "text_message"


def test_unknown_type_falls_back_to_default() -> None:
    msg = parse_chat_message({"id": "x", "type": "something_new", "content": "hi"})
    assert msg["message_type"] == "text_message"


def test_non_dict_payload_raises() -> None:
    with pytest.raises(ParsingError):
        parse_chat_message(["not", "a", "dict"])


def test_missing_id_raises() -> None:
    with pytest.raises(ParsingError):
        parse_chat_message({"content": "no id"})


def test_missing_content_yields_empty_message() -> None:
    msg = parse_chat_message({"id": "x", "content": None})
    assert msg["message"] == ""
    assert "emotes" not in msg


def test_invalid_timestamp_is_omitted() -> None:
    msg = parse_chat_message({"id": "x", "created_at": "not-a-date"})
    assert "timestamp" not in msg


def test_missing_sender_yields_no_author() -> None:
    msg = parse_chat_message({"id": "x", "content": "hi"})
    assert "author" not in msg


def test_sender_not_dict_yields_no_author() -> None:
    msg = parse_chat_message({"id": "x", "sender": "nope"})
    assert "author" not in msg


def test_author_without_identity_or_slug() -> None:
    msg = parse_chat_message({"id": "x", "sender": {"id": 7, "username": "OnlyName"}})
    assert msg["author"] == {"id": "7", "display_name": "OnlyName", "name": "onlyname"}


def test_author_empty_sender_is_dropped() -> None:
    msg = parse_chat_message({"id": "x", "sender": {}})
    assert "author" not in msg


def test_identity_without_color_or_badges() -> None:
    msg = parse_chat_message(
        {"id": "x", "sender": {"id": 1, "identity": {"color": "", "badges": "bad"}}}
    )
    assert "colour" not in msg["author"]
    assert "badges" not in msg["author"]


def test_badges_skip_malformed_entries() -> None:
    raw = {
        "id": "x",
        "sender": {
            "id": 1,
            "identity": {
                "badges": [
                    "not-a-dict",
                    {},
                    {"type": "vip"},
                    {"text": "no-type"},
                ]
            },
        },
    }
    msg = parse_chat_message(raw)
    assert msg["author"]["badges"] == [
        {"name": "vip"},
        {"title": "no-type"},
    ]


def test_parse_preloaded_skips_unparseable() -> None:
    parsed = parse_preloaded_messages(
        [{"id": "ok", "content": "hi"}, {"no": "id"}, "garbage"]
    )
    assert [m["message_id"] for m in parsed] == ["ok"]


# --- int-id coercion regression (Round-13) -----------------------------------
# Kick sends numeric ids in some contexts.  _opt_str must coerce them to str
# rather than rejecting them (which get_str would do).  Pin the invariant so
# a future accessor swap cannot silently break numeric-id handling.


def test_top_level_numeric_id_coerced_to_str() -> None:
    """parse_chat_message accepts a numeric top-level id and stringifies it."""
    msg = parse_chat_message({"id": 12345, "content": "hi"})
    assert msg["message_id"] == "12345"


def test_top_level_numeric_id_in_preloaded() -> None:
    """parse_preloaded_messages coerces numeric ids in a batch."""
    parsed = parse_preloaded_messages(
        [{"id": 99, "content": "a"}, {"id": "str", "content": "b"}]
    )
    assert [m["message_id"] for m in parsed] == ["99", "str"]

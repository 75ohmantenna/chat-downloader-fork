# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing import messages
from chat_downloader.sites.kick.parsing.messages import (
    parse_chat_message,
    parse_preloaded_messages,
)
from tests.kick_helpers import load_fixture


@pytest.fixture
def captured(monkeypatch):
    samples = []
    monkeypatch.setattr(
        messages,
        "capture_debug_sample",
        lambda *args, **kwargs: samples.append((args, kwargs)),
    )
    return samples


def _identity_message(identity):
    return {"id": "x", "sender": {"id": 1, "identity": identity}}


def test_parse_full_chat_message() -> None:
    raw = load_fixture("chat_message_event_data.json")
    msg = parse_chat_message(raw)
    assert msg["message_id"] == "live-1"
    assert msg["message_type"] == "text_message"
    assert msg["message"] == "hello world :PogU:"
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


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            "chat_message_badges_v2.json",
            [
                {"name": "moderator", "title": "Moderator"},
                {"name": "subscriber", "title": "Subscriber", "count": 36},
            ],
        ),
        (
            "chat_message_badges_v2_selected.json",
            [
                {
                    "name": "level",
                    "badge_type": "global",
                    "icons": [{"url": "https://example.test/chat/badges/21.png"}],
                    "selected": True,
                    "metadata": {"level": 21},
                    "sort_order": 1,
                },
                {"name": "subscriber", "title": "Subscriber", "count": 5},
            ],
        ),
    ],
)
def test_modern_badge_fixtures_are_parsed_without_mutation(fixture, expected):
    raw = load_fixture(fixture)
    original = json.loads(json.dumps(raw))
    assert parse_chat_message(raw)["author"]["badges"] == expected
    assert raw == original


def test_parse_modern_badge_metadata_is_not_aliased() -> None:
    raw = _identity_message(
        {"badges_v2": [{"name": "level", "metadata": {"nested": {"level": 28}}}]}
    )
    msg = parse_chat_message(raw)
    msg["author"]["badges"][0]["metadata"]["nested"]["level"] = 29

    assert raw["sender"]["identity"]["badges_v2"][0]["metadata"] == {
        "nested": {"level": 28}
    }


def test_reply_type_maps_to_text_message() -> None:
    msg = parse_chat_message({"id": "x", "type": "reply", "content": "hi"})
    assert msg["message_type"] == "text_message"


@pytest.mark.parametrize("encoded", [False, True])
def test_celebration_preserves_subscription_renewal_metadata(encoded) -> None:
    raw = load_fixture("celebration_message_event_data.json")
    if encoded:
        raw["metadata"] = json.dumps(raw["metadata"])

    msg = parse_chat_message(raw)

    assert msg["message_type"] == "text_message"
    assert msg["message"] == "Celebrating 20 months!"
    assert msg["metadata"] == {
        "celebration": {
            "id": "celebration-renewal-1",
            "type": "subscription_renewed",
            "total_months": 20,
            "created_at": 1787880598835777,
        }
    }


@pytest.mark.parametrize(
    ("kind", "metadata", "content", "omitted"),
    [
        *[
            ("celebration", value, "Visible message", "metadata")
            for value in [
                None,
                [],
                "{bad json",
                {"celebration": []},
                {"celebration": {}},
            ]
        ],
        (
            "celebration",
            {
                "celebration": {
                    "id": " ",
                    "type": 7,
                    "total_months": True,
                    "created_at": "not-a-date",
                }
            },
            None,
            "metadata",
        ),
        *[
            ("reply", value, "text", "in_reply_to")
            for value in ["{bad json", "[]", [], 7, None]
        ],
    ],
)
def test_malformed_message_metadata_is_ignored(kind, metadata, content, omitted):
    raw = {"id": kind, "type": kind, "metadata": metadata}
    if content is not None:
        raw["content"] = content
    msg = parse_chat_message(raw)
    assert msg["message_type"] == "text_message"
    assert omitted not in msg


@pytest.mark.parametrize("encoded", [False, True])
def test_reply_preserves_original_message_context(encoded) -> None:
    raw = load_fixture("reply_message_event_data.json")
    if encoded:
        raw["metadata"] = json.dumps(raw["metadata"])

    msg = parse_chat_message(raw)

    assert msg["message_type"] == "text_message"
    assert msg["in_reply_to"]["message_id"] == "original-message"
    assert msg["in_reply_to"]["message"] == "Original :KEKW:"
    assert msg["in_reply_to"]["emotes"][0]["id"] == "37226"
    assert msg["in_reply_to"]["timestamp"] == 1787650140000000
    assert msg["in_reply_to"]["author"]["display_name"] == "OriginalAuthor"
    assert msg["in_reply_to"]["thread_parent_message_id"] == "original-message"


def test_reply_uses_original_message_sender_fallback() -> None:
    raw = {
        "id": "reply",
        "type": "reply",
        "metadata": {
            "original_message": {
                "id": "original",
                "sender": {"id": 1, "username": "NestedAuthor"},
            }
        },
    }

    msg = parse_chat_message(raw)

    assert msg["in_reply_to"]["author"]["display_name"] == "NestedAuthor"


def test_unknown_type_is_captured_and_falls_back_to_default(captured) -> None:
    msg = parse_chat_message({"id": "x", "type": "something_new", "content": "hi"})

    assert msg["message_type"] == "text_message"
    assert captured[0][0][0] == "kick-unknown-message-type"
    assert captured[0][0][1]["message_type"] == "something_new"
    assert captured[0][1]["sample_limit"] == 10


@pytest.mark.parametrize("payload", [["not", "a", "dict"], {"content": "no id"}])
def test_invalid_payload_raises(payload) -> None:
    with pytest.raises(ParsingError):
        parse_chat_message(payload)


def test_malformed_preloaded_message_is_captured(captured) -> None:
    assert parse_preloaded_messages([{"content": "missing id"}]) == []

    assert captured[0][0][0] == "kick-malformed-preloaded-message"
    assert captured[0][0][1]["raw"] == {"content": "missing id"}


def test_missing_content_yields_empty_message() -> None:
    msg = parse_chat_message({"id": "x", "content": None})
    assert msg["message"] == ""
    assert "emotes" not in msg


@pytest.mark.parametrize("created_at", ["", "not-a-date", 123])
def test_invalid_timestamp_is_omitted(created_at: object) -> None:
    msg = parse_chat_message({"id": "x", "created_at": created_at})
    assert "timestamp" not in msg


@pytest.mark.parametrize(
    "fields", [{"content": "hi"}, {"sender": "nope"}, {"sender": {}}]
)
def test_missing_or_invalid_sender_yields_no_author(fields) -> None:
    assert "author" not in parse_chat_message({"id": "x", **fields})


def test_author_without_identity_or_slug() -> None:
    msg = parse_chat_message({"id": "x", "sender": {"id": 7, "username": "OnlyName"}})
    assert msg["author"] == {"id": "7", "display_name": "OnlyName", "name": "onlyname"}


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ({"color": "", "badges": "bad"}, {"id": "1"}),
        (
            {
                "badges_v2": [
                    {
                        "name": "level",
                        "image_url": "https://example.test/level.png",
                        "selected": True,
                    }
                ]
            },
            {
                "id": "1",
                "badges": [
                    {
                        "name": "level",
                        "icons": [{"url": "https://example.test/level.png"}],
                        "selected": True,
                    }
                ],
            },
        ),
        (
            {"badges": ["not-a-dict", {}, {"type": "vip"}, {"text": "no-type"}]},
            {"id": "1", "badges": [{"name": "vip"}, {"title": "no-type"}]},
        ),
        (
            {
                "badges": [
                    {"type": "moderator", "sort_order": 12},
                    {"type": "subscriber", "count": True, "sort_order": 9},
                ]
            },
            {
                "id": "1",
                "badges": [
                    {"name": "moderator"},
                    {"name": "subscriber", "count": True},
                ],
            },
        ),
        (
            {
                "badges_v2": [
                    "not-a-dict",
                    {},
                    {
                        "name": 7,
                        "badge_type": [],
                        "image_url": {},
                        "selected": "yes",
                        "metadata": [],
                        "sort_order": True,
                    },
                    {"name": "level", "sort_order": 2},
                ]
            },
            {"id": "1", "badges": [{"name": "level", "sort_order": 2}]},
        ),
        (
            {
                "badges": [
                    {"type": "moderator", "sort_order": 1},
                    {"type": "subscriber", "sort_order": 5},
                ],
                "badges_v2": [
                    {"name": "level", "selected": True},
                    {"name": "achievement", "selected": True, "sort_order": 5},
                ],
            },
            {
                "id": "1",
                "badges": [
                    {"name": "level", "selected": True},
                    {"name": "moderator"},
                    {"name": "subscriber"},
                    {"name": "achievement", "selected": True, "sort_order": 5},
                ],
            },
        ),
        (
            {"badges": [{"type": "moderator", "sort_order": 12}], "badges_v2": "bad"},
            {"id": "1", "badges": [{"name": "moderator"}]},
        ),
        (
            {"badges_v2": [{"name": "level", "metadata": {}}]},
            {"id": "1", "badges": [{"name": "level", "metadata": {}}]},
        ),
    ],
)
def test_badge_normalization(identity, expected) -> None:
    assert parse_chat_message(_identity_message(identity))["author"] == expected


@pytest.mark.parametrize(
    ("badge", "expected"),
    [
        ({"name": "level", "selected": True}, [{"name": "level", "selected": True}]),
        ({"name": "level", "selected": False}, []),
        ({"name": "level"}, [{"name": "level"}]),
        ({"name": "level", "selected": "yes"}, [{"name": "level"}]),
        ({"name": "level", "active": True}, [{"name": "level"}]),
        ({"name": "level", "active": False}, []),
        (
            {"name": "level", "selected": True, "active": False},
            [{"name": "level", "selected": True}],
        ),
    ],
)
def test_modern_badge_selection(
    badge: object,
    expected: list[dict[str, object]],
) -> None:
    msg = parse_chat_message(_identity_message({"badges_v2": [badge]}))

    assert msg["author"].get("badges", []) == expected


@pytest.mark.parametrize(
    "badge",
    [
        {"selected": True},
        {"metadata": {"level": 1}},
        {"sort_order": 3},
        {"image_url": "https://example.test/level.png"},
        {"name": ""},
        {"name": "   ", "selected": True},
    ],
)
def test_modern_badges_require_nonempty_name(badge: object) -> None:
    msg = parse_chat_message(_identity_message({"badges_v2": [badge]}))

    assert "badges" not in msg["author"]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([{"id": "ok", "content": "hi"}, {"no": "id"}, "garbage"], ["ok"]),
        ([{"id": 99, "content": "a"}, {"id": "str", "content": "b"}], ["99", "str"]),
    ],
)
def test_preloaded_skips_invalid_messages_and_coerces_numeric_ids(rows, expected):
    assert [m["message_id"] for m in parse_preloaded_messages(rows)] == expected


# Kick sends numeric IDs in some contexts. _opt_str must coerce them to str
# rather than rejecting them (which get_str would do). Pin the invariant so
# a future accessor swap cannot silently break numeric-id handling.


def test_top_level_numeric_id_coerced_to_str() -> None:
    """parse_chat_message accepts a numeric top-level id and stringifies it."""
    msg = parse_chat_message({"id": 12345, "content": "hi"})
    assert msg["message_id"] == "12345"

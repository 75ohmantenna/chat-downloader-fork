# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing import messages
from chat_downloader.sites.kick.parsing.messages import (
    iter_preloaded_messages,
    parse_chat_message,
)
from tests.kick_helpers import load_fixture, raw_message


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
    return raw_message("x", sender={"id": 1, "identity": identity})


def test_parse_full_chat_message():
    msg = parse_chat_message(load_fixture("chat_message_event_data.json"))
    assert (
        msg["message_id"],
        msg["message_type"],
        msg["message"],
        msg["timestamp"],
    ) == (
        "live-1",
        "text_message",
        "hello world :PogU:",
        1704067260000000,
    )
    author = msg["author"]
    assert (author["id"], author["display_name"], author["name"], author["colour"]) == (
        "99",
        "LiveUser",
        "liveuser",
        "#FF0000",
    )
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


def test_modern_badge_metadata_is_not_aliased():
    raw = _identity_message(
        {"badges_v2": [{"name": "level", "metadata": {"nested": {"level": 28}}}]}
    )
    msg = parse_chat_message(raw)
    msg["author"]["badges"][0]["metadata"]["nested"]["level"] = 29
    metadata = raw["sender"]["identity"]["badges_v2"][0]["metadata"]
    assert metadata == {"nested": {"level": 28}}


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("kind", ["celebration", "reply"])
def test_message_metadata_context(kind, encoded):
    raw = load_fixture(f"{kind}_message_event_data.json")
    if encoded:
        raw["metadata"] = json.dumps(raw["metadata"])
    msg = parse_chat_message(raw)
    assert msg["message_type"] == "text_message"
    if kind == "celebration":
        assert msg["message"] == "Celebrating 20 months!"
        assert msg["metadata"] == {
            "celebration": {
                "id": "celebration-renewal-1",
                "type": "subscription_renewed",
                "total_months": 20,
                "created_at": 1787880598835777,
            }
        }
    else:
        reply = msg["in_reply_to"]
        assert reply["message_id"] == "original-message"
        assert reply["message"] == "Original :KEKW:"
        assert reply["emotes"][0]["id"] == "37226"
        assert reply["timestamp"] == 1787650140000000
        assert reply["author"]["display_name"] == "OriginalAuthor"
        assert reply["thread_parent_message_id"] == "original-message"


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


def test_reply_uses_original_message_sender_fallback():
    msg = parse_chat_message(
        raw_message(
            "reply",
            type="reply",
            metadata={
                "original_message": {
                    "id": "original",
                    "sender": {"id": 1, "username": "NestedAuthor"},
                },
            },
        )
    )
    assert msg["in_reply_to"]["author"]["display_name"] == "NestedAuthor"


def test_unknown_type_is_captured_and_falls_back_to_default(captured):
    msg = parse_chat_message(raw_message("x", type="something_new"))
    assert msg["message_type"] == "text_message"
    assert captured[0][0][0] == "kick-unknown-message-type"
    assert captured[0][0][1]["message_type"] == "something_new"
    assert captured[0][1]["sample_limit"] == 10


@pytest.mark.parametrize(
    "payload",
    [["not", "a", "dict"], {"content": "no id"}],
)
def test_invalid_payload_raises(payload):
    with pytest.raises(ParsingError):
        parse_chat_message(payload)


def test_malformed_preloaded_message_is_captured(captured):
    assert list(iter_preloaded_messages([{"content": "missing id"}])) == []
    assert captured[0][0][0] == "kick-malformed-preloaded-message"
    assert captured[0][0][1]["raw"] == {"content": "missing id"}
    assert captured[0][1]["sample_limit"] == 10


@pytest.mark.parametrize(
    ("fields", "expected", "omitted"),
    [
        ({"type": "reply", "content": "hi"}, {"message_type": "text_message"}, set()),
        ({"content": None}, {"message": ""}, {"emotes"}),
        *[
            ({"created_at": value}, {}, {"timestamp"})
            for value in ["", "not-a-date", 123]
        ],
        *[
            (fields, {}, {"author"})
            for fields in [{"content": "hi"}, {"sender": "nope"}, {"sender": {}}]
        ],
        (
            {"sender": {"id": 7, "username": "OnlyName"}},
            {"author": {"id": "7", "display_name": "OnlyName", "name": "onlyname"}},
            set(),
        ),
        ({"id": 12345, "content": "hi"}, {"message_id": "12345"}, set()),
    ],
)
def test_message_optional_fields_and_id_coercion(fields, expected, omitted):
    msg = parse_chat_message({"id": "x", **fields})
    for key, value in expected.items():
        assert msg[key] == value
    assert not omitted & msg.keys()


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
                        "selected": True,
                        "icons": [{"url": "https://example.test/level.png"}],
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
def test_badge_normalization(identity, expected):
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
def test_modern_badge_selection(badge, expected):
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
def test_modern_badges_require_nonempty_name(badge):
    msg = parse_chat_message(_identity_message({"badges_v2": [badge]}))
    assert "badges" not in msg["author"]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([raw_message("ok"), {"no": "id"}, "garbage"], ["ok"]),
        (
            [raw_message(99, content="a"), raw_message("str", content="b")],
            ["99", "str"],
        ),
    ],
)
def test_preloaded_skips_invalid_messages_and_coerces_numeric_ids(rows, expected):
    assert [m["message_id"] for m in iter_preloaded_messages(rows)] == expected


def test_preloaded_list_api_preserves_exports_order_and_eager_parsing(captured):
    from chat_downloader.sites.kick import parsing
    from chat_downloader.sites.kick.parsing.messages import parse_preloaded_messages

    assert "parse_preloaded_messages" in parsing.__all__
    assert parsing.parse_preloaded_messages is parse_preloaded_messages
    rows = load_fixture("preloaded_messages.json")["data"]["messages"]
    result = parse_preloaded_messages(iter([rows[0], {"content": "no id"}, rows[1]]))

    assert isinstance(result, list)
    assert [item["message_id"] for item in result] == ["preloaded-2", "preloaded-1"]
    assert len(captured) == 1
    assert captured[0][1]["sample_limit"] == 10
    assert result == list(iter_preloaded_messages(rows))

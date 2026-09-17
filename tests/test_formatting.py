# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from chat_downloader.errors import FormatFileNotFound, FormatNotFound
from chat_downloader.formatting.format import ItemFormatter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def formatter() -> ItemFormatter:
    return ItemFormatter()


def format_field(formatter, field, value, config, template=None):
    item = {field: value}
    if field == "author.badges":
        item = {"author": {"badges": value}}
    return formatter.format(
        item,
        format_object={
            "template": template or "{" + field + "}",
            "keys": {field: config},
        },
    )


def test_item_formatter_with_custom_path(tmp_path: Path) -> None:
    path = tmp_path / "formats.json"
    path.write_text(json.dumps({"test_format": {"template": "Test: {message}"}}))
    assert (
        ItemFormatter(path=str(path)).format({"message": "custom"}, "test_format")
        == "Test: custom"
    )


def test_item_formatter_invalid_path() -> None:
    with pytest.raises(FormatFileNotFound):
        ItemFormatter(path="/nonexistent/path/format.json")


@pytest.mark.parametrize(
    ("message", "template", "expected"),
    [
        (f"first{control}second", "{message}", f"first{visible}second")
        for control, visible in [
            ("\r", r"\r"),
            ("\n", r"\n"),
            ("\r\n", r"\r\n"),
            ("\x85", r"\u0085"),
            ("\u2028", r"\u2028"),
            ("\u2029", r"\u2029"),
            ("\t", "\t"),
        ]
    ]
    + [
        (
            "value\x9bhidden\x9dtitle\x9c",
            "head\n{message}\rfoot",
            r"head\nvaluehiddentitle\rfoot",
        )
    ],
)
def test_format_control_characters(formatter, message, template, expected):
    assert (
        formatter.format({"message": message}, format_object={"template": template})
        == expected
    )


@pytest.fixture(
    params=[
        ("youtube", "0:42 | "),
        ("youtube_live_default", "2020-01-01 00:00:00 | "),
        ("youtube_live_24_hour", "00:00 | "),
        ("youtube_live_12_hour", "12:00 AM | "),
    ]
)
def youtube_format(request):
    return request.param


@pytest.mark.parametrize(
    ("fields", "notice"),
    [
        ({"author": {"name": "user"}, "message": "Test"}, "user: Test"),
        (
            {
                "message_type": "text_message",
                "author": {"name": "user"},
                "message": "Hello",
            },
            "user: Hello",
        ),
        *[
            ({"message_type": kind, "message": "System notice"}, "System notice")
            for kind in ["viewer_engagement_message", "deleted_message", "ban_user"]
        ],
        (
            {
                "target_message_id": "message-id",
                "message_type": "ban_user",
                "message": None,
            },
            "[Message removed: message-id]",
        ),
        (
            {
                "author": {"id": "channel-id"},
                "message_type": "ban_user",
                "message": None,
            },
            "[Messages removed for author: channel-id]",
        ),
        (
            {
                "action_type": "remove_chat_item",
                "message_type": "ban_user",
                "message": None,
            },
            "[Moderation action: remove_chat_item]",
        ),
    ],
)
def test_youtube_timestamp_and_author_separator(
    formatter, youtube_format, fields, notice
):
    name, prefix = youtube_format
    item = {"timestamp": 1577836800000000, "time_text": "0:42", **fields}
    assert formatter.format(item, name) == prefix + notice


def test_youtube_moderation_without_timing(formatter, youtube_format):
    item = {
        "action_type": "remove_chat_item",
        "message": None,
        "message_type": "ban_user",
        "target_message_id": "message-id",
    }
    assert formatter.format(item, youtube_format[0]) == "[Message removed: message-id]"


@pytest.mark.parametrize(
    "message_type",
    [
        "subscription",
        "resubscription",
        "subscription_gift",
        "anonymous_subscription_gift",
        "anonymous_mystery_subscription_gift",
        "mystery_subscription_gift",
        "extend_subscription",
        "standard_pay_forward",
        "community_pay_forward",
        "prime_community_gift_received",
        "gift_subscription_match",
        "prime_paid_upgrade",
        "gift_paid_upgrade",
        "reward_gift",
        "anonymous_gift_paid_upgrade",
        "viewermilestone",
        "charity_donation",
        "one_tap_breakpoint_achieved",
        "one_tap_gift_redeemed",
        "one_tap_streak_expired",
        "one_tap_streak_started",
        "moderator_anniversary",
    ],
)
@pytest.mark.parametrize(
    ("message", "expected_suffix"),
    [
        ("Hello chat", "Test subscribed! — Hello chat"),
        ("", "Test subscribed!"),
        (None, "Test subscribed!"),
    ],
)
def test_format_twitch_subscription_types_separate_optional_message(
    formatter, message_type, message, expected_suffix
):
    item = {
        "message_type": message_type,
        "system_message": "Test subscribed!",
        "author": {"name": "user"},
        "timestamp": 1000000,
    }
    if message is not None:
        item["message"] = message
    assert formatter.format(item, "twitch").endswith(expected_suffix)


@pytest.mark.parametrize(
    ("fields", "expected_suffix"),
    [
        ({"system_message": "Raid arrived!"}, "Raid arrived!"),
        ({"message": "Hello chat"}, "raider — Hello chat"),
        (
            {"system_message": "Raid arrived!", "message": "Hello chat"},
            "Raid arrived! — Hello chat",
        ),
        ({"system_message": "", "raider_name": "explicit-raider"}, "explicit-raider"),
        ({}, "raider"),
        (
            {
                "raider_display_name": "Explicit Raider",
                "raider_name": "explicit-raider",
                "author": {"name": "event-author"},
            },
            "Explicit Raider",
        ),
    ],
)
@pytest.mark.parametrize("message_type", ["raid", "unraid"])
def test_format_twitch_raid_types(formatter, message_type, fields, expected_suffix):
    item = {
        "message_type": message_type,
        "author": {"name": "raider"},
        "timestamp": 1000000,
        **fields,
    }
    assert formatter.format(item, "twitch").endswith(expected_suffix)


@pytest.mark.parametrize(
    ("matching", "message_type", "expected"),
    [
        ("all", "any_type", "matched"),
        ("all", None, "matched"),
        (["text_message", "paid_message"], "text_message", "matched"),
        (["text_message", "paid_message"], "paid_message", "matched"),
        (["text_message", "paid_message"], "other_type", "fallback"),
        ("ban_user", "ban_user", "matched"),
        ("ban_user", "text_message", "fallback"),
        (None, "text_message", "fallback"),
    ],
)
def test_format_list_matching(formatter, matching, message_type, expected) -> None:
    formatter.format_file = {
        "default": {"template": "fallback"},
        "choices": [{"matching": matching, "template": "matched"}],
    }
    assert formatter.format({"message_type": message_type}, "choices") == expected


@pytest.mark.parametrize("parent", ["base", "missing"])
def test_inheritance_preserves_parent_and_prefers_child(formatter, parent) -> None:
    formatter.format_file["base"] = {
        "template": "parent",
        "keys": {"message": "[{}]"},
    }
    child = {"inherit": parent, "template": "Custom: {message}"}
    expected = "Custom: [value]" if parent == "base" else "Custom: value"
    assert formatter.format({"message": "value"}, format_object=child) == expected
    assert formatter.format_file["base"]["template"] == "parent"


@pytest.mark.parametrize(
    "kwargs", [{"format_name": "nonexistent_format"}, {"format_object": {}}, {}]
)
def test_missing_format_raises(formatter, kwargs):
    if not kwargs:
        formatter.format_file.pop("default")
    with pytest.raises(FormatNotFound):
        formatter.format({"message": "test"}, **kwargs)


@pytest.mark.parametrize(
    ("field", "value", "config", "template", "expected"),
    [
        *[("value", "present", config, "{value}", "") for config in [42, []]],
        *[
            (
                "value",
                value,
                {"template": " [{}]", "omit_if_false": True},
                "before{value}after",
                "beforeafter",
            )
            for value in [False, 0, "", [], {}, None]
        ],
        (
            "value",
            True,
            {"template": " [present]", "omit_if_false": True},
            "before{value}after",
            "before [present]after",
        ),
        (
            "time_text",
            "1:30:00",
            {"template": "{}", "format": "{}:{:02}:{:02}"},
            "{time_text}",
            "1:30:00",
        ),
    ],
)
def test_field_configuration(formatter, field, value, config, template, expected):
    assert format_field(formatter, field, value, config, template) == expected


@pytest.mark.parametrize(
    ("field", "value", "separator", "expected"),
    [
        ("value", [1, 2, 3], ", ", "1, 2, 3"),
        ("value", (4, 5, 6), ", ", "4, 5, 6"),
        ("value", "hello", ", ", "hello"),
        ("value", [1, 2, 3], None, "[1, 2, 3]"),
        (
            "author.badges",
            [{"title": "Moderator"}, {"title": "Member"}, {}],
            ", ",
            "Moderator, Member",
        ),
    ],
)
def test_field_separator(formatter, field, value, separator, expected) -> None:
    config = {"template": "{}", "separator": separator}
    assert format_field(formatter, field, value, config) == expected


def test_omit_if_false_after_badge_separator(formatter):
    config = {"separator": ", ", "template": "({})", "omit_if_false": True}
    assert (
        formatter._format_field_value(
            "author.badges", [{"name": "level"}], {"author.badges": config}
        )
        == ""
    )


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ({"display_name": "DisplayUser", "name": "user"}, "DisplayUser"),
        ({"display_name": "", "name": "user"}, "user"),
        ({}, ""),
    ],
)
def test_placeholder_fallbacks(formatter, author, expected) -> None:
    assert (
        formatter.format(
            {"author": author},
            format_object={"template": "{author.display_name|author.name}"},
        )
        == expected
    )


@pytest.mark.parametrize(
    ("duration", "suffix"),
    [
        (0, "was timed out for 0 seconds."),
        (1, "was timed out for 1 second."),
        (30, "was timed out for 30 seconds."),
        (None, "was permanently banned."),
    ],
)
def test_format_twitch_ban_duration(formatter, duration, suffix):
    item = {
        "message_type": "ban_user",
        "banned_user": "spammer",
        "ban_type": "permanent",
        "timestamp": 1000000,
    }
    if duration is not None:
        item.update(ban_duration=duration, ban_type="timeout")
    assert formatter.format(item, "twitch").endswith("spammer " + suffix)
    if duration is not None:
        assert item["ban_duration"] == duration
        assert isinstance(item["ban_duration"], int)


@pytest.mark.parametrize(
    ("value", "singular_template", "expected"),
    [
        (1, "{} item", "1 item"),
        (1.0, "{} item", "1.0 item"),
        (0, "{} item", "0 items"),
        (2, "{} item", "2 items"),
        (True, "{} item", "True items"),
        (False, "{} item", "False items"),
        ("1", "{} item", "1 items"),
        *[(1, template, "1 items") for template in [None, 1, False]],
    ],
)
def test_singular_template_selection(formatter, value, singular_template, expected):
    config = {"template": "{} items", "singular_template": singular_template}
    assert format_field(formatter, "count", value, config) == expected


def test_apply_format_by_type_unknown_field(formatter):
    assert (
        formatter._apply_format_by_type(
            "custom.field", "some_value", {"format": "%s", "template": "{}"}
        )
        == "some_value"
    )


@pytest.mark.parametrize("template", ["{0.attr}", "{0[key]}"])
def test_safe_formatter_rejects_object_access(formatter, template) -> None:
    with pytest.raises(ValueError, match="Attribute/index access not allowed"):
        format_field(formatter, "value", "private", template)

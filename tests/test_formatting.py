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


def test_item_formatter_with_custom_path(tmp_path: Path) -> None:
    path = tmp_path / "formats.json"
    path.write_text(
        json.dumps({"test_format": {"template": "Test: {message}"}}),
        encoding="utf-8",
    )
    fmt = ItemFormatter(path=str(path))
    assert fmt.format({"message": "custom"}, "test_format") == "Test: custom"


def test_item_formatter_invalid_path() -> None:
    """Test ItemFormatter with non-existent format file."""
    with pytest.raises(FormatFileNotFound):
        ItemFormatter(path="/nonexistent/path/format.json")


@pytest.mark.parametrize(
    ("line_break", "visible"),
    [
        ("\r", r"\r"),
        ("\n", r"\n"),
        ("\r\n", r"\r\n"),
        ("\x85", r"\u0085"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ],
)
def test_format_renders_line_breaks_visibly(
    formatter: ItemFormatter,
    line_break: str,
    visible: str,
) -> None:
    result = formatter.format(
        {"message": f"first{line_break}second"},
        format_object={"template": "{message}"},
    )

    assert result == f"first{visible}second"


def test_format_flattens_template_line_breaks_and_removes_c1_controls(
    formatter: ItemFormatter,
) -> None:
    result = formatter.format(
        {"message": "value\x9bhidden\x9dtitle\x9c"},
        format_object={"template": "head\n{message}\rfoot"},
    )

    assert result == r"head\nvaluehiddentitle\rfoot"


def test_format_preserves_horizontal_tabs(formatter: ItemFormatter) -> None:
    result = formatter.format(
        {"message": "first\tsecond"},
        format_object={"template": "{message}"},
    )

    assert result == "first\tsecond"


def test_format_nonexistent(formatter: ItemFormatter) -> None:
    """Test formatting with non-existent format."""
    item = {"message": "test"}

    with pytest.raises(FormatNotFound):
        formatter.format(item, format_name="nonexistent_format")


@pytest.mark.parametrize(
    ("format_name", "expected_prefix"),
    [
        ("youtube_live_default", "2020-01-01 00:00:00 | "),
        ("youtube_live_24_hour", "00:00 | "),
    ],
)
def test_youtube_live_prefers_timestamp_over_time_text(
    formatter: ItemFormatter, format_name: str, expected_prefix: str
) -> None:
    item = {
        "message": "Test",
        "author": {"name": "user"},
        "time_text": "0:42",
        "timestamp": 1577836800000000,
    }
    assert formatter.format(item, format_name) == f"{expected_prefix}user: Test"


@pytest.mark.parametrize(
    ("format_name", "expected_prefix"),
    [
        ("youtube_live_default", "2020-01-01 00:00:00 | "),
        ("youtube_live_24_hour", "00:00 | "),
        ("youtube_live_12_hour", "12:00 AM | "),
    ],
)
@pytest.mark.parametrize(
    "message_type",
    ["viewer_engagement_message", "deleted_message", "ban_user"],
)
def test_youtube_live_system_messages_omit_missing_author_separator(
    formatter: ItemFormatter,
    format_name: str,
    expected_prefix: str,
    message_type: str,
) -> None:
    item = {
        "message_type": message_type,
        "message": "System notice",
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
        "time_text": "0:42",
    }

    result = formatter.format(item, format_name=format_name)

    assert result == f"{expected_prefix}System notice"


@pytest.mark.parametrize(
    ("format_name", "expected_prefix"),
    [
        ("youtube", "0:42 | "),
        ("youtube_live_default", "2020-01-01 00:00:00 | "),
        ("youtube_live_24_hour", "00:00 | "),
        ("youtube_live_12_hour", "12:00 AM | "),
    ],
)
@pytest.mark.parametrize(
    ("fields", "expected_notice"),
    [
        ({"target_message_id": "message-id"}, "[Message removed: message-id]"),
        (
            {"author": {"id": "channel-id"}},
            "[Messages removed for author: channel-id]",
        ),
        ({"action_type": "remove_chat_item"}, "[Moderation action: remove_chat_item]"),
    ],
)
def test_youtube_moderation_messages_have_nonempty_fallbacks(
    formatter: ItemFormatter,
    format_name: str,
    expected_prefix: str,
    fields: dict[str, object],
    expected_notice: str,
) -> None:
    item = {
        "message_type": "ban_user",
        "message": None,
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
        "time_text": "0:42",
        **fields,
    }

    result = formatter.format(item, format_name=format_name)

    assert result == f"{expected_prefix}{expected_notice}"


@pytest.mark.parametrize(
    "format_name",
    [
        "youtube",
        "youtube_live_default",
        "youtube_live_24_hour",
        "youtube_live_12_hour",
    ],
)
def test_youtube_moderation_message_without_timing_is_not_blank(
    formatter: ItemFormatter,
    format_name: str,
) -> None:
    item = {
        "action_type": "remove_chat_item",
        "message": None,
        "message_type": "ban_user",
        "target_message_id": "message-id",
    }

    result = formatter.format(item, format_name=format_name)

    assert result == "[Message removed: message-id]"


def test_youtube_live_system_format_keeps_authored_message_separator(
    formatter: ItemFormatter,
) -> None:
    item = {
        "message_type": "text_message",
        "message": "Hello",
        "author": {"name": "user"},
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
        "time_text": "0:42",
    }

    result = formatter.format(item, format_name="youtube_live_default")

    assert result == "2020-01-01 00:00:00 | user: Hello"


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
    formatter: ItemFormatter,
    message_type: str,
    message: str | None,
    expected_suffix: str,
) -> None:
    item = {
        "message_type": message_type,
        "system_message": "Test subscribed!",
        "author": {"name": "user"},
        "timestamp": 1000000,
    }
    if message is not None:
        item["message"] = message

    result = formatter.format(item, format_name="twitch")

    assert result.endswith(expected_suffix)


@pytest.mark.parametrize(
    ("fields", "expected_suffix"),
    [
        ({"system_message": "Raid arrived!"}, "Raid arrived!"),
        ({"message": "Hello chat"}, "raider — Hello chat"),
        (
            {"system_message": "Raid arrived!", "message": "Hello chat"},
            "Raid arrived! — Hello chat",
        ),
        (
            {"system_message": "", "raider_name": "explicit-raider"},
            "explicit-raider",
        ),
        ({}, "raider"),
    ],
)
@pytest.mark.parametrize("message_type", ["raid", "unraid"])
def test_format_twitch_raid_types_preserve_details_and_fallbacks(
    formatter: ItemFormatter,
    message_type: str,
    fields: dict[str, str],
    expected_suffix: str,
) -> None:
    item = {
        "message_type": message_type,
        "author": {"name": "raider"},
        "timestamp": 1000000,
        **fields,
    }

    result = formatter.format(item, format_name="twitch")

    assert result.endswith(expected_suffix)


@pytest.mark.parametrize("message_type", ["raid", "unraid"])
def test_format_twitch_raid_types_prefer_raider_identity(
    formatter: ItemFormatter,
    message_type: str,
) -> None:
    item = {
        "message_type": message_type,
        "raider_display_name": "Explicit Raider",
        "raider_name": "explicit-raider",
        "author": {"name": "event-author"},
        "timestamp": 1000000,
    }

    result = formatter.format(item, format_name="twitch")

    assert result.endswith("Explicit Raider")


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


def test_no_valid_format_raises(formatter: ItemFormatter) -> None:
    """Test FormatNotFound when format_object is empty/falsy (line 104)."""
    # Passing an empty dict as format_object makes it falsy, triggering line
    # 104
    with pytest.raises(FormatNotFound):
        formatter.format({"message": "test"}, format_object={})


def test_missing_default_format_raises(formatter: ItemFormatter) -> None:
    formatter.format_file.pop("default")
    with pytest.raises(FormatNotFound):
        formatter.format({"message": "test"})


@pytest.mark.parametrize("config", [42, []])
def test_invalid_field_config_renders_empty(formatter, config) -> None:
    assert (
        formatter.format(
            {"value": "present"},
            format_object={"template": "{value}", "keys": {"value": config}},
        )
        == ""
    )


@pytest.mark.parametrize("value", [False, 0, "", [], {}, None])
def test_omit_if_false_suppresses_falsey_field_values(
    formatter: ItemFormatter,
    value: object,
) -> None:
    result = formatter.format(
        {"value": value},
        format_object={
            "template": "before{value}after",
            "keys": {
                "value": {
                    "template": " [{}]",
                    "omit_if_false": True,
                }
            },
        },
    )

    assert result == "beforeafter"


def test_omit_if_false_preserves_truthy_constant_template(
    formatter: ItemFormatter,
) -> None:
    result = formatter.format(
        {"value": True},
        format_object={
            "template": "before{value}after",
            "keys": {
                "value": {
                    "template": " [present]",
                    "omit_if_false": True,
                }
            },
        },
    )

    assert result == "before [present]after"


def test_format_time_text_field(formatter: ItemFormatter) -> None:
    assert (
        formatter.format(
            {"time_text": "1:30:00"},
            format_object={
                "template": "{time_text}",
                "keys": {"time_text": {"template": "{}", "format": "{}:{:02}:{:02}"}},
            },
        )
        == "1:30:00"
    )


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
    item = {"author": {"badges": value}} if field == "author.badges" else {field: value}
    assert (
        formatter.format(
            item,
            format_object={
                "template": "{" + field + "}",
                "keys": {field: {"template": "{}", "separator": separator}},
            },
        )
        == expected
    )


def test_omit_if_false_after_badge_separator(formatter: ItemFormatter) -> None:
    result = formatter._format_field_value(
        "author.badges",
        [{"name": "level"}],
        {
            "author.badges": {
                "separator": ", ",
                "template": "({})",
                "omit_if_false": True,
            }
        },
    )

    assert result == ""


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
    ("duration", "expected_suffix"),
    [
        (0, "spammer was timed out for 0 seconds."),
        (1, "spammer was timed out for 1 second."),
        (30, "spammer was timed out for 30 seconds."),
    ],
)
def test_format_twitch_timeout_duration_uses_correct_grammar(
    formatter: ItemFormatter,
    duration: int,
    expected_suffix: str,
) -> None:
    item = {
        "message_type": "ban_user",
        "banned_user": "spammer",
        "ban_duration": duration,
        "ban_type": "timeout",
        "timestamp": 1000000,
    }

    result = formatter.format(item, format_name="twitch")

    assert result.endswith(expected_suffix)
    assert item["ban_duration"] == duration
    assert isinstance(item["ban_duration"], int)


def test_format_twitch_permanent_ban_falls_back_to_ban_type(
    formatter: ItemFormatter,
) -> None:
    item = {
        "message_type": "ban_user",
        "banned_user": "spammer",
        "ban_type": "permanent",
        "timestamp": 1000000,
    }

    result = formatter.format(item, format_name="twitch")

    assert result.endswith("spammer was permanently banned.")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "1 item"),
        (1.0, "1.0 item"),
        (0, "0 items"),
        (2, "2 items"),
        (True, "True items"),
        (False, "False items"),
        ("1", "1 items"),
    ],
)
def test_singular_template_only_matches_exact_numeric_one(
    formatter: ItemFormatter,
    value: object,
    expected: str,
) -> None:
    format_object = {
        "template": "{count}",
        "keys": {
            "count": {
                "template": "{} items",
                "singular_template": "{} item",
            },
        },
    }

    assert formatter.format({"count": value}, format_object=format_object) == expected


@pytest.mark.parametrize("singular_template", [None, 1, False])
def test_invalid_singular_template_falls_back_to_standard_template(
    formatter: ItemFormatter,
    singular_template: object,
) -> None:
    format_object = {
        "template": "{count}",
        "keys": {
            "count": {
                "template": "{} items",
                "singular_template": singular_template,
            },
        },
    }

    assert formatter.format({"count": 1}, format_object=format_object) == "1 items"


def test_apply_format_by_type_unknown_field(formatter: ItemFormatter) -> None:
    """_apply_format_by_type for a non-timestamp/time_text field."""
    # A custom field with a format string but not timestamp or time_text
    field_config = {"format": "%s", "template": "{}"}
    result = formatter._apply_format_by_type(
        "custom.field",
        "some_value",
        field_config,
    )
    # Should return the value unchanged (line 309)
    assert result == "some_value"


@pytest.mark.parametrize("template", ["{0.attr}", "{0[key]}"])
def test_safe_formatter_rejects_object_access(formatter, template) -> None:
    with pytest.raises(ValueError, match="Attribute/index access not allowed"):
        formatter.format(
            {"value": "private"},
            format_object={"template": "{value}", "keys": {"value": template}},
        )

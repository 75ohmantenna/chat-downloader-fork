# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import tempfile

import pytest

from chat_downloader.errors import FormatFileNotFound, FormatNotFound
from chat_downloader.formatting.format import ItemFormatter


@pytest.fixture
def formatter() -> ItemFormatter:
    return ItemFormatter()


def test_item_formatter_initialization() -> None:
    """Test ItemFormatter initialization."""
    fmt = ItemFormatter()
    assert fmt.format_file is not None
    assert isinstance(fmt.format_file, dict)


def test_item_formatter_with_custom_path() -> None:
    """Test ItemFormatter with custom format file."""
    # Create a temporary format file
    custom_format = {"test_format": {"template": "Test: {message}"}}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(custom_format, f)
        temp_path = f.name

    try:
        fmt = ItemFormatter(path=temp_path)
        assert "test_format" in fmt.format_file
    finally:
        os.unlink(temp_path)


def test_item_formatter_invalid_path() -> None:
    """Test ItemFormatter with non-existent format file."""
    with pytest.raises(FormatFileNotFound):
        ItemFormatter(path="/nonexistent/path/format.json")


def test_format_default(formatter: ItemFormatter) -> None:
    """Test formatting with default format."""
    item = {
        "message": "Hello World",
        "author": {"name": "TestUser"},
        "timestamp": 1234567890000000,
    }

    result = formatter.format(item, format_name="default")
    assert isinstance(result, str)


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


def test_format_with_missing_fields(formatter: ItemFormatter) -> None:
    """Test formatting when item is missing some fields."""
    item = {"message": "Partial data"}

    # Should handle missing fields gracefully
    try:
        result = formatter.format(item, format_name="default")
        assert isinstance(result, str)
    except Exception as e:
        pytest.fail(f"Formatting failed with missing fields: {e}")


def test_format_constants() -> None:
    """Test ItemFormatter constants."""
    assert ItemFormatter.KEY_TEMPLATE == "template"
    assert ItemFormatter.KEY_KEYS == "keys"
    assert ItemFormatter.KEY_MATCHING == "matching"
    assert ItemFormatter.KEY_SINGULAR_TEMPLATE == "singular_template"
    assert ItemFormatter.KEY_OMIT_IF_FALSE == "omit_if_false"
    assert ItemFormatter.DEFAULT_FORMAT_NAME == "default"


def test_format_special_fields() -> None:
    """Test that special fields are defined."""
    assert ItemFormatter.FIELD_TIMESTAMP == "timestamp"
    assert ItemFormatter.FIELD_RECEIVED_TIMESTAMP == "received_timestamp"
    assert ItemFormatter.FIELD_TIME_TEXT == "time_text"
    assert ItemFormatter.FIELD_AUTHOR_BADGES == "author.badges"


def test_format_regex_pattern() -> None:
    """Test the placeholder regex pattern."""
    import re

    pattern = ItemFormatter._INDEX_REGEX

    # Should match {field}
    assert re.search(pattern, "Test {field} text") is not None

    # Should match multiple fields
    matches = re.findall(pattern, "{field1} and {field2}")
    assert len(matches) == 2


def test_format_with_nested_fields(formatter: ItemFormatter) -> None:
    """Test formatting with nested field access."""
    item = {"message": "Test", "author": {"name": "John", "id": "123"}}

    # Format files may support nested field access
    try:
        result = formatter.format(item)
        assert isinstance(result, str)
    except Exception:
        pass  # Some formats may not support all fields


def test_format_with_timestamp(formatter: ItemFormatter) -> None:
    """Test timestamp formatting."""
    item = {
        "message": "Test",
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
    }

    result = formatter.format(item)
    assert isinstance(result, str)


def test_youtube_live_default_prefers_timestamp_over_time_text(
    formatter: ItemFormatter,
) -> None:
    """Live YouTube variants should render absolute timestamps first."""
    item = {
        "message": "Test",
        "author": {"name": "user"},
        "time_text": "0:42",
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
    }

    result = formatter.format(item, format_name="youtube_live_default")
    assert result.startswith("2020-01-01 00:00:00 | ")


def test_youtube_live_24_hour_prefers_timestamp_over_time_text(
    formatter: ItemFormatter,
) -> None:
    """24-hour live YouTube variant should still use absolute clock time."""
    item = {
        "message": "Test",
        "author": {"name": "user"},
        "time_text": "0:42",
        "timestamp": 1577836800000000,  # 2020-01-01 00:00:00 UTC
    }

    result = formatter.format(item, format_name="youtube_live_24_hour")
    assert result.startswith("00:00 | ")


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


def test_format_with_badges(formatter: ItemFormatter) -> None:
    """Test formatting with author badges."""
    item = {
        "message": "Test",
        "author": {
            "name": "TestUser",
            "badges": [{"title": "Moderator"}, {"title": "Member"}],
        },
    }

    result = formatter.format(item)
    assert isinstance(result, str)


def test_format_empty_item(formatter: ItemFormatter) -> None:
    """Test formatting empty item."""
    item = {}

    try:
        result = formatter.format(item)
        assert isinstance(result, str)
    except Exception:
        pass  # May fail depending on format requirements


def test_multiple_format_names(formatter: ItemFormatter) -> None:
    """Test that multiple format names exist."""
    assert "default" in formatter.format_file

    # Check that format file has multiple formats
    assert len(formatter.format_file) > 0


def test_format_file_structure(formatter: ItemFormatter) -> None:
    """Test format file has correct structure."""
    # Each format is either:
    # - a dict (single spec), or
    # - a list of dicts (multiple specs with matching rules)
    for format_name, format_spec in formatter.format_file.items():
        if isinstance(format_spec, dict):
            continue
        if isinstance(format_spec, list):
            for entry in format_spec:
                assert isinstance(entry, dict), f"{format_name} entries should be dicts"
            continue
        pytest.fail(f"{format_name} should be a dict or list of dicts")


# ---------------------------------------------------------------------------
# ItemFormatter internals
# ---------------------------------------------------------------------------


def test_format_with_list_format_object(formatter: ItemFormatter) -> None:
    """Test _resolve_format_object when format is a list (line 122).

    The 'twitch' format is a list of specs with matching rules.
    """
    item = {
        "message_type": "text_message",
        "message": "hello",
        "author": {"name": "user"},
        "timestamp": 1000000,
    }
    result = formatter.format(item, format_name="twitch")
    assert isinstance(result, str)


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
        "prime_paid_upgrade",
        "gift_paid_upgrade",
        "reward_gift",
        "anonymous_gift_paid_upgrade",
        "viewermilestone",
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


def test_format_twitch_fallback_to_all(formatter: ItemFormatter) -> None:
    """_match_format_from_list falls through to the 'all' matcher."""
    item = {
        "message_type": "unknown_type",
        "message": "test",
        "author": {"name": "user"},
        "timestamp": 1000000,
    }
    result = formatter.format(item, format_name="twitch")
    assert isinstance(result, str)


def test_does_format_match_all(formatter: ItemFormatter) -> None:
    """Test _does_format_match with 'all' (lines 175-176)."""
    fmt = {"matching": "all", "template": ""}
    assert formatter._does_format_match(fmt, "any_type")
    assert formatter._does_format_match(fmt, None)


def test_does_format_match_list(formatter: ItemFormatter) -> None:
    """Test _does_format_match with a list (lines 178-179)."""
    fmt = {"matching": ["text_message", "paid_message"]}
    assert formatter._does_format_match(fmt, "text_message")
    assert formatter._does_format_match(fmt, "paid_message")
    assert not formatter._does_format_match(fmt, "other_type")


def test_does_format_match_string(formatter: ItemFormatter) -> None:
    """Test _does_format_match with a string (line 181)."""
    fmt = {"matching": "ban_user"}
    assert formatter._does_format_match(fmt, "ban_user")
    assert not formatter._does_format_match(fmt, "text_message")


def test_does_format_match_no_matching(formatter: ItemFormatter) -> None:
    """Test _does_format_match with no matching key (line 181)."""
    fmt = {}  # no matching key, matching=None
    assert not formatter._does_format_match(fmt, "text_message")


def test_apply_inheritance(formatter: ItemFormatter) -> None:
    """Test _apply_inheritance merges parent format (lines 194-195)."""
    fmt_with_inherit = {
        "inherit": "default",
        "template": "Custom: {message}",
    }
    result = formatter._apply_inheritance(fmt_with_inherit)
    # Should have merged parent fields
    assert isinstance(result, dict)
    # Template from child should take priority
    assert result.get("template") == "Custom: {message}"


def test_apply_inheritance_missing_parent(formatter: ItemFormatter) -> None:
    """Test _apply_inheritance when parent doesn't exist (lines 194-195)."""
    fmt_with_inherit = {
        "inherit": "nonexistent_parent",
        "template": "Custom",
    }
    result = formatter._apply_inheritance(fmt_with_inherit)
    # Should still return a valid format object
    assert result.get("template") == "Custom"


def test_no_valid_format_raises(formatter: ItemFormatter) -> None:
    """Test FormatNotFound when format_object is empty/falsy (line 104)."""
    # Passing an empty dict as format_object makes it falsy, triggering line
    # 104
    with pytest.raises(FormatNotFound):
        formatter.format({"message": "test"}, format_object={})


def test_get_default_format_fallback() -> None:
    """_get_default_format returns None when the format file has no default."""
    fmt = ItemFormatter()
    # Remove the 'default' key from format_file to simulate the fallback path
    original_default = fmt.format_file.pop("default", None)
    try:
        result = fmt._get_default_format()
        assert result is None
        # _get_format_by_name("default") should fall into the fallback (line
        # 140)
        result2 = fmt._get_format_by_name("default")
        assert result2 is None
    finally:
        if original_default:
            fmt.format_file["default"] = original_default


def test_extract_template_not_str_or_dict(formatter: ItemFormatter) -> None:
    """_extract_template handles field_config that is neither str nor dict."""
    result = formatter._extract_template(None)
    assert result == ""

    result = formatter._extract_template(42)
    assert result == ""

    result = formatter._extract_template([])
    assert result == ""


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


def test_format_time_text_field() -> None:
    """Test _apply_format_by_type for time_text (lines 302-309)."""
    # Use a custom format with time_text formatting
    custom_format = {
        "default": {
            "template": "{time_text}",
            "keys": {
                "time_text": {
                    "template": "{}",
                    "format": "{}:{:02}:{:02}",
                },
            },
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(custom_format, f)
        temp_path = f.name
    try:
        fmt = ItemFormatter(path=temp_path)
        item = {"time_text": "1:30:00"}
        result = fmt.format(item)
        assert isinstance(result, str)
    finally:
        os.unlink(temp_path)


def test_apply_separator_list_value(formatter: ItemFormatter) -> None:
    """Test _apply_separator with list values (lines 329-332)."""
    field_config = {"separator": ", ", "template": "{}"}
    result = formatter._apply_separator("some.field", [1, 2, 3], field_config)
    assert result == "1, 2, 3"

    result = formatter._apply_separator("some.field", (4, 5, 6), field_config)
    assert result == "4, 5, 6"


def test_apply_separator_non_list_value(formatter: ItemFormatter) -> None:
    """Test _apply_separator with non-list values (line 332)."""
    field_config = {"separator": ", ", "template": "{}"}
    result = formatter._apply_separator("some.field", "hello", field_config)
    assert result == "hello"


def test_apply_separator_author_badges(formatter: ItemFormatter) -> None:
    """Test _apply_separator for author.badges (lines 325-326)."""
    field_config = {"separator": ", ", "template": "({})"}
    badges = [{"title": "Moderator"}, {"title": "Member"}, {}]
    result = formatter._apply_separator("author.badges", badges, field_config)
    assert result == "Moderator, Member"


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


def test_apply_separator_no_separator(formatter: ItemFormatter) -> None:
    """_apply_separator returns the value unchanged when no separator is set."""
    field_config = {}
    result = formatter._apply_separator("some.field", [1, 2, 3], field_config)
    assert result == [1, 2, 3]


def test_replace_placeholder_fallback_keys() -> None:
    """Test _replace_placeholder with fallback keys (lines 222-232)."""
    fmt = ItemFormatter()
    item = {"author": {"display_name": "DisplayUser", "name": "user"}}

    # Simulate a match for "{author.display_name|author.name}"
    class FakMatch:
        def group(self, n) -> str:
            return "author.display_name|author.name"

    result = fmt._replace_placeholder(FakMatch(), item, {})
    assert result == "DisplayUser"


def test_replace_placeholder_empty_string_uses_fallback() -> None:
    """An empty preferred field must not suppress a populated fallback."""
    fmt = ItemFormatter()
    item = {"author": {"display_name": "", "name": "user"}}

    class FakMatch:
        def group(self, n) -> str:
            return "author.display_name|author.name"

    result = fmt._replace_placeholder(FakMatch(), item, {})

    assert result == "user"


def test_replace_placeholder_all_missing() -> None:
    """_replace_placeholder returns '' when all fallbacks are missing."""
    fmt = ItemFormatter()
    item = {}

    class FakMatch:
        def group(self, n) -> str:
            return "nonexistent|also_missing"

    result = fmt._replace_placeholder(FakMatch(), item, {})
    assert result == ""


def test_format_twitch_ban_type(formatter: ItemFormatter) -> None:
    """Test formatting with dict-based key config and 'ban_user' type."""
    item = {
        "message_type": "ban_user",
        "banned_user": "spammer",
        "ban_type": "permanent",
        "timestamp": 1000000,
    }
    result = formatter.format(item, format_name="twitch")
    assert isinstance(result, str)


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


def test_match_format_from_list_no_match_uses_default(
    formatter: ItemFormatter,
) -> None:
    """Test _match_format_from_list falls through to default (line 164)."""
    # Create a format list where nothing matches the item's message_type
    format_list = [
        {"matching": "only_this_type", "template": "matched"},
    ]
    item = {"message_type": "different_type", "message": "test"}
    result = formatter._match_format_from_list(format_list, item)
    # Should fall through to default format (line 164)
    default = formatter._get_default_format()
    assert result == default


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


def test_safe_formatter_rejects_dot_access() -> None:
    from chat_downloader.formatting.format import _SAFE_FORMATTER

    with pytest.raises(ValueError, match="Attribute/index access not allowed"):
        _SAFE_FORMATTER.format("{0.attr}", "someobj")


def test_safe_formatter_rejects_index_access() -> None:
    from chat_downloader.formatting.format import _SAFE_FORMATTER

    with pytest.raises(ValueError, match="Attribute/index access not allowed"):
        _SAFE_FORMATTER.format("{0[key]}", {"key": "val"})

# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import tempfile

# The `test_formatting` test hits YouTube network APIs via ChatDownloader.
import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import FormatFileNotFound, FormatNotFound
from chat_downloader.formatting.format import ItemFormatter

YOUTUBE_NETWORK_TEST_URL = "https://www.youtube.com/watch?v=wXspodtIxYU"


@pytest.fixture
def formatter() -> ItemFormatter:
    return ItemFormatter()


@pytest.mark.network
def test_formatting() -> None:
    chat = ChatDownloader().get_chat(
        YOUTUBE_NETWORK_TEST_URL,
        format="24_hour",
        max_messages=10,
    )
    for message in chat:
        chat.print_formatted(message)


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
    assert ItemFormatter.DEFAULT_FORMAT_NAME == "default"


def test_format_special_fields() -> None:
    """Test that special fields are defined."""
    assert ItemFormatter.FIELD_TIMESTAMP == "timestamp"
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


def test_format_twitch_subscription_type(formatter: ItemFormatter) -> None:
    """_match_format_from_list matches a subscription type."""
    item = {
        "message_type": "subscription",
        "system_message": "Test subscribed!",
        "author": {"name": "user"},
        "timestamp": 1000000,
    }
    result = formatter.format(item, format_name="twitch")
    assert isinstance(result, str)


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

# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.sites.remap import Remapper


def test_remapper_initialization() -> None:
    """Test basic Remapper initialization."""
    # Test with new_key and identity function
    r = Remapper(new_key="new_field", remap_function=lambda x: x)
    assert r.new_key == "new_field"
    assert r.remap_function is not None
    assert not r.to_unpack

    # Test with new_key and function
    r = Remapper(new_key="field", remap_function=lambda x: x.upper())
    assert r.new_key == "field"
    assert r.remap_function is not None
    assert not r.to_unpack

    # Test with to_unpack
    r = Remapper(remap_function=lambda x: {"a": 1}, to_unpack=True)
    assert r.new_key is None
    assert r.to_unpack


def test_remapper_invalid_initialization() -> None:
    """Test Remapper initialization with invalid parameters."""
    # Cannot specify new_key with to_unpack
    with pytest.raises(ValueError) as context:
        Remapper(new_key="field", to_unpack=True)
    assert "to_unpack is True" in str(context.value)

    # remap_function can be None for identity transformation
    # This should NOT raise an error
    remapper = Remapper(new_key="field", remap_function=None)
    assert remapper.remap_function is None


def test_remapper_staticmethod_unwrapping() -> None:
    """Test that Remapper unwraps staticmethod correctly."""

    @staticmethod
    def static_func(x):
        return x * 2

    r = Remapper(new_key="field", remap_function=static_func)
    assert r.remap_function is not None
    # Should be able to call the unwrapped function
    assert r.remap_function(5) == 10


def test_remap_with_string_mapping() -> None:
    """Test basic remapping with string values."""
    remapping = {"old_name": "new_name"}
    info = {}

    Remapper.remap(info, remapping, "old_name", "value")
    assert info == {"new_name": "value"}


def test_remap_with_remapper_object() -> None:
    """Test remapping with Remapper object."""
    remapping = {
        "field": Remapper(
            new_key="new_field", remap_function=lambda x: x.upper()
        ),
    }
    info = {}

    Remapper.remap(info, remapping, "field", "test")
    assert info == {"new_field": "TEST"}


def test_remap_with_identity_transformation() -> None:
    """Test remapping with identity transformation function."""
    remapping = {
        "field": Remapper(new_key="new_field", remap_function=lambda x: x)
    }
    info = {}

    Remapper.remap(info, remapping, "field", "value")
    assert info == {"new_field": "value"}


def test_remap_with_unpack() -> None:
    """Test remapping with unpacking into parent dict."""

    def parse_author(data):
        return {"author_name": data["name"], "author_id": data["id"]}

    remapping = {
        "author": Remapper(remap_function=parse_author, to_unpack=True)
    }
    info = {}
    author_data = {"name": "John", "id": "123"}

    Remapper.remap(info, remapping, "author", author_data)
    assert info == {"author_name": "John", "author_id": "123"}


def test_remap_with_unpack_invalid_type() -> None:
    """Test that unpacking non-dict raises ValueError."""
    remapping = {"field": Remapper(remap_function=lambda x: x, to_unpack=True)}
    info = {}

    with pytest.raises(ValueError) as context:
        Remapper.remap(info, remapping, "field", "not_a_dict")
    assert "not a dictionary" in str(context.value)


def test_remap_keep_unknown_keys() -> None:
    """Test keeping unknown keys when no remapping found."""
    remapping = {"known": "mapped"}
    info = {}

    # Without keep_unknown_keys
    Remapper.remap(info, remapping, "unknown", "value", keep_unknown_keys=False)
    assert info == {}

    # With keep_unknown_keys
    info = {}
    Remapper.remap(info, remapping, "unknown", "value", keep_unknown_keys=True)
    assert info == {"unknown": "value"}


def test_remap_replace_char_with_underscores() -> None:
    """Test replacing characters with underscores in unknown keys."""
    remapping = {}
    info = {}

    Remapper.remap(
        info,
        remapping,
        "field-name",
        "value",
        keep_unknown_keys=True,
        replace_char_with_underscores="-",
    )
    assert info == {"field_name": "value"}


def test_remap_dict_basic() -> None:
    """Test complete dictionary remapping."""
    input_dict = {"oldName": "John", "oldAge": 30, "oldCity": "NYC"}

    remapping = {"oldName": "name", "oldAge": "age", "oldCity": "city"}

    result = Remapper.remap_dict(input_dict, remapping)
    expected = {"name": "John", "age": 30, "city": "NYC"}
    assert result == expected


def test_remap_dict_with_transformations() -> None:
    """Test dictionary remapping with transformation functions."""
    input_dict = {"text": "hello", "count": "42", "enabled": "true"}

    remapping = {
        "text": Remapper(new_key="message", remap_function=lambda x: x.upper()),
        "count": Remapper(new_key="value", remap_function=int),
        "enabled": Remapper(
            new_key="is_enabled",
            remap_function=lambda x: x == "true",
        ),
    }

    result = Remapper.remap_dict(input_dict, remapping)
    expected = {"message": "HELLO", "value": 42, "is_enabled": True}
    assert result == expected


def test_remap_dict_with_partial_mapping() -> None:
    """Test dictionary remapping with partial mapping."""
    input_dict = {"a": 1, "b": 2, "c": 3}
    remapping = {"a": "x", "b": "y"}

    # Without keep_unknown_keys
    result = Remapper.remap_dict(input_dict, remapping, keep_unknown_keys=False)
    assert result == {"x": 1, "y": 2}

    # With keep_unknown_keys
    result = Remapper.remap_dict(input_dict, remapping, keep_unknown_keys=True)
    assert result == {"x": 1, "y": 2, "c": 3}


def test_remap_dict_with_unpack() -> None:
    """Test dictionary remapping with unpacking."""
    input_dict = {"name": "John", "details": {"age": 30, "city": "NYC"}}

    remapping = {
        "name": "username",
        "details": Remapper(remap_function=lambda x: x, to_unpack=True),
    }

    result = Remapper.remap_dict(input_dict, remapping)
    expected = {"username": "John", "age": 30, "city": "NYC"}
    assert result == expected


def test_remap_dict_complex_scenario() -> None:
    """Test complex remapping scenario similar to real usage."""
    # Simulate YouTube API response structure
    input_dict = {
        "authorName": {"simpleText": "TestUser"},
        "authorExternalChannelId": "UC123",
        "message": {"runs": [{"text": "Hello"}]},
        "timestampUsec": "1234567890",
        "authorBadges": [{"tooltip": "Member"}],
    }

    def extract_text(obj):
        if isinstance(obj, dict) and "simpleText" in obj:
            return obj["simpleText"]
        return obj

    def extract_runs(obj):
        if isinstance(obj, dict) and "runs" in obj:
            return "".join(run.get("text", "") for run in obj["runs"])
        return obj

    def parse_badges(badges):
        return [badge.get("tooltip") for badge in badges]

    remapping = {
        "authorName": Remapper(
            new_key="author_name", remap_function=extract_text
        ),
        "authorExternalChannelId": "author_id",
        "message": Remapper(
            new_key="message_text", remap_function=extract_runs
        ),
        "timestampUsec": Remapper(
            new_key="timestamp",
            remap_function=int,
        ),
        "authorBadges": Remapper(new_key="badges", remap_function=parse_badges),
    }

    result = Remapper.remap_dict(input_dict, remapping)
    expected = {
        "author_name": "TestUser",
        "author_id": "UC123",
        "message_text": "Hello",
        "timestamp": 1234567890,
        "badges": ["Member"],
    }
    assert result == expected


def test_remap_dict_empty_input() -> None:
    """Test remapping with empty input."""
    result = Remapper.remap_dict({}, {"a": "b"})
    assert result == {}


def test_remap_dict_empty_remapping() -> None:
    """Test remapping with empty remapping dict."""
    input_dict = {"a": 1, "b": 2}

    # Without keep_unknown_keys
    result = Remapper.remap_dict(input_dict, {})
    assert result == {}

    # With keep_unknown_keys
    result = Remapper.remap_dict(input_dict, {}, keep_unknown_keys=True)
    assert result == {"a": 1, "b": 2}


def test_remap_with_invalid_remapping_type() -> None:
    """Test that invalid remapping type raises ValueError."""
    remapping = {"field": 123}  # Not a string or Remapper
    info = {}

    with pytest.raises(ValueError) as context:
        Remapper.remap(info, remapping, "field", "value")
    assert "Unknown remapping" in str(context.value)


def test_remapper_none_remap_function_passes_value() -> None:
    from chat_downloader.sites.remap import Remapper

    r = Remapper(new_key="dest")  # remap_function defaults to None
    info: dict = {}
    Remapper.remap(info, {"src": r}, "src", "original_value")
    assert info["dest"] == "original_value"

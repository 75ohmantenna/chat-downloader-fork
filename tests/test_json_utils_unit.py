# SPDX-License-Identifier: MIT

"""Comprehensive unit tests for json_utils module to improve coverage."""

from __future__ import annotations

from chat_downloader.utils.json_utils import (
    flatten_json,
    nested_update,
    try_parse_json,
)


def test_try_parse_json_valid_dict() -> None:
    """Test parsing valid JSON object."""
    result = try_parse_json('{"key": "value", "num": 123}')
    assert result == {"key": "value", "num": 123}


def test_try_parse_json_valid_array() -> None:
    """Test parsing valid JSON array."""
    result = try_parse_json('[1, 2, 3, "four"]')
    assert result == [1, 2, 3, "four"]


def test_try_parse_json_invalid() -> None:
    """Test parsing invalid JSON (lines 11)."""
    result = try_parse_json("not valid json")
    assert result is None


def test_try_parse_json_with_default() -> None:
    """Test parsing with custom default (line 12)."""
    result = try_parse_json("invalid", default={})
    assert result == {}

    result = try_parse_json("invalid", default=[])
    assert result == []

    result = try_parse_json("invalid", default="error")
    assert result == "error"


def test_try_parse_json_none_input() -> None:
    """Test parsing None input (line 11)."""
    result = try_parse_json(None)
    assert result is None

    result = try_parse_json(None, default="default")
    assert result == "default"


def test_try_parse_json_type_error() -> None:
    """Test TypeError handling (line 11)."""
    # Integer input should trigger TypeError
    result = try_parse_json(123, default="error")
    assert result == "error"


def test_flatten_json_simple_dict() -> None:
    """Test flattening simple dictionary (lines 18-21)."""
    original = {"a": 1, "b": 2, "c": 3}
    result = flatten_json(original)
    assert result == {"a": 1, "b": 2, "c": 3}


def test_flatten_json_nested_dict() -> None:
    """Test flattening nested dictionary (lines 18-21)."""
    original = {"a": {"b": {"c": 1}}}
    result = flatten_json(original)
    assert result == {"a.b.c": 1}


def test_flatten_json_with_list() -> None:
    """Test flattening with lists (lines 22-24)."""
    original = {"items": [1, 2, 3]}
    result = flatten_json(original)
    assert result == {"items.0": 1, "items.1": 2, "items.2": 3}


def test_flatten_json_complex() -> None:
    """Test flattening complex nested structure (lines 18-26)."""
    original = {
        "user": {
            "name": "John",
            "age": 30,
            "addresses": [{"city": "NYC"}, {"city": "LA"}],
        },
        "settings": {
            "theme": "dark",
            "notifications": {"email": True, "sms": False},
        },
    }
    result = flatten_json(original)

    assert result["user.name"] == "John"
    assert result["user.age"] == 30
    assert result["user.addresses.0.city"] == "NYC"
    assert result["user.addresses.1.city"] == "LA"
    assert result["settings.theme"] == "dark"
    assert result["settings.notifications.email"]
    assert not result["settings.notifications.sms"]


def test_flatten_json_empty_dict() -> None:
    """Test flattening empty dictionary."""
    result = flatten_json({})
    assert result == {}


def test_flatten_json_empty_list() -> None:
    """Test flattening empty list."""
    result = flatten_json([])
    assert result == {}


def test_flatten_json_list_input() -> None:
    """Test flattening with list as root (lines 22-24)."""
    original = [{"a": 1}, {"b": 2}, {"c": 3}]
    result = flatten_json(original)
    assert result["0.a"] == 1
    assert result["1.b"] == 2
    assert result["2.c"] == 3


def test_flatten_json_mixed_types() -> None:
    """Test flattening with mixed value types (line 26)."""
    original = {
        "string": "value",
        "number": 42,
        "float": 3.14,
        "bool": True,
        "null": None,
        "nested": {"inner": "data"},
    }
    result = flatten_json(original)

    assert result["string"] == "value"
    assert result["number"] == 42
    assert result["float"] == 3.14
    assert result["bool"]
    assert result["null"] is None
    assert result["nested.inner"] == "data"


def test_flatten_json_truncates_extreme_depth() -> None:
    nested = value = {}
    for _ in range(52):
        value["x"] = {}
        value = value["x"]
    value["leaf"] = "end"

    result = flatten_json(nested)
    assert any(key.endswith("x") for key in result)
    assert any(isinstance(v, str) and "leaf" in v for v in result.values())


def test_flatten_json_root_truncation_uses_namespaced_key() -> None:
    result = flatten_json({"leaf": "end"})
    assert "__truncated__" not in result


def test_nested_update_simple() -> None:
    """Test simple nested update (lines 34-42)."""
    original = {"a": 1, "b": 2}
    update = {"c": 3}
    result = nested_update(original, update)
    assert result == {"a": 1, "b": 2, "c": 3}


def test_nested_update_overwrite() -> None:
    """Test nested update with overwrite (line 42)."""
    original = {"a": 1, "b": 2}
    update = {"b": 20}
    result = nested_update(original, update)
    assert result == {"a": 1, "b": 20}


def test_nested_update_nested_dict() -> None:
    """Test nested update with nested dictionaries (lines 35-38)."""
    original = {
        "user": {"name": "John", "age": 30},
        "settings": {"theme": "light"},
    }
    update = {
        "user": {"age": 31, "city": "NYC"},
        "settings": {"notifications": True},
    }

    result = nested_update(original, update)

    # Original nested values should be preserved
    assert result["user"]["name"] == "John"
    # Updated values should be changed
    assert result["user"]["age"] == 31
    # New nested values should be added
    assert result["user"]["city"] == "NYC"
    # Original nested dict should be preserved
    assert result["settings"]["theme"] == "light"
    # New nested values should be added
    assert result["settings"]["notifications"]


def test_nested_update_replace_non_dict() -> None:
    """Test nested update replacing non-dict with dict (lines 37-40)."""
    original = {"a": 1, "b": "string"}
    update = {"b": {"nested": "value"}}
    result = nested_update(original, update)

    # Non-dict value should be replaced with dict
    assert result["a"] == 1
    assert result["b"] == {"nested": "value"}


def test_nested_update_replace_dict_with_non_dict() -> None:
    """Test nested update replacing dict with non-dict (line 42)."""
    original = {"a": 1, "b": {"nested": "value"}}
    update = {"b": "string"}
    result = nested_update(original, update)

    # Dict value should be replaced with non-dict
    assert result["a"] == 1
    assert result["b"] == "string"


def test_nested_update_deep_nesting() -> None:
    """Test nested update with deep nesting (lines 35-38)."""
    original = {"level1": {"level2": {"level3": {"value": 1}}}}
    update = {"level1": {"level2": {"level3": {"value": 2, "new": 3}}}}

    result = nested_update(original, update)

    assert result["level1"]["level2"]["level3"]["value"] == 2
    assert result["level1"]["level2"]["level3"]["new"] == 3


def test_nested_update_empty_dict() -> None:
    """Test nested update with empty dictionaries."""
    original = {}
    update = {"a": 1}
    result = nested_update(original, update)
    assert result == {"a": 1}

    original = {"a": 1}
    update = {}
    result = nested_update(original, update)
    assert result == {"a": 1}


def test_nested_update_modifies_original() -> None:
    """Test that nested_update modifies the original dict."""
    original = {"a": 1}
    update = {"b": 2}
    result = nested_update(original, update)

    # Should return the same object
    assert result is original
    assert original == {"a": 1, "b": 2}


def test_nested_update_mapping_check() -> None:
    """Test nested update with collections.abc.Mapping check (line 35)."""
    from collections import OrderedDict

    original = {"a": {"b": 1}}
    # OrderedDict is a Mapping
    update = {"a": OrderedDict([("b", 2), ("c", 3)])}

    result = nested_update(original, update)

    # Should merge nested dict properly
    assert result["a"]["b"] == 2
    assert result["a"]["c"] == 3

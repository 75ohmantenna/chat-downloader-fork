# SPDX-License-Identifier: MIT

"""Comprehensive unit tests for dict_utils module to improve coverage."""

from __future__ import annotations

from chat_downloader.utils.dict_utils import (
    move_to_dict,
    multi_get,
    try_get_first_key,
    try_get_first_value,
)


def test_multi_get_dict_single_key() -> None:
    """Test multi_get with dictionary and single key (line 9-10)."""
    data = {"a": 1, "b": 2, "c": 3}
    assert multi_get(data, "a") == 1
    assert multi_get(data, "b") == 2


def test_multi_get_dict_nested() -> None:
    """Test multi_get with nested dictionaries (line 9-10)."""
    data = {"user": {"name": "John", "details": {"age": 30}}}
    assert multi_get(data, "user", "name") == "John"
    assert multi_get(data, "user", "details", "age") == 30


def test_multi_get_dict_missing_key() -> None:
    """Test multi_get with missing key (line 10)."""
    data = {"a": 1, "b": 2}
    assert multi_get(data, "c") is None
    assert multi_get(data, "a", "nested") is None


def test_multi_get_dict_with_default() -> None:
    """Test multi_get with custom default (line 10)."""
    data = {"a": 1}
    assert multi_get(data, "b", default="N/A") == "N/A"
    assert multi_get(data, "a", "nested", default=0) == 0


def test_multi_get_list_with_int_key() -> None:
    """Test multi_get with list and integer index (lines 11-14)."""
    data = [10, 20, 30]
    assert multi_get(data, 0) == 10
    assert multi_get(data, 1) == 20
    assert multi_get(data, 2) == 30


def test_multi_get_list_out_of_bounds() -> None:
    """Test multi_get with list index out of bounds (lines 13-15)."""
    data = [10, 20, 30]
    assert multi_get(data, 5) is None
    assert multi_get(data, 10, default="error") == "error"


def test_multi_get_tuple_with_int_key() -> None:
    """Test multi_get with tuple and integer index (lines 11-14)."""
    data = (10, 20, 30)
    assert multi_get(data, 0) == 10
    assert multi_get(data, 1) == 20
    assert multi_get(data, 2) == 30


def test_multi_get_mixed_dict_list() -> None:
    """Test multi_get with mixed dict and list (lines 9-14)."""
    data = {"items": [{"name": "item1"}, {"name": "item2"}]}
    assert multi_get(data, "items", 0, "name") == "item1"
    assert multi_get(data, "items", 1, "name") == "item2"


def test_multi_get_mixed_list_dict() -> None:
    """Test multi_get with list containing dicts (lines 11-14)."""
    data = [{"a": 1}, {"b": 2}, {"c": 3}]
    assert multi_get(data, 0, "a") == 1
    assert multi_get(data, 1, "b") == 2
    assert multi_get(data, 2, "c") == 3


def test_multi_get_invalid_type() -> None:
    """Test multi_get with invalid type (lines 16-17)."""
    # String is not dict, list, or tuple
    data = "string"
    assert multi_get(data, 0) is None
    assert multi_get(data, 0, default="error") == "error"


def test_multi_get_dict_with_string_key_on_list() -> None:
    """Test multi_get with string key on list (lines 16-17)."""
    data = {"items": [1, 2, 3]}
    # Trying to use string key on list should return default
    assert multi_get(data, "items", "invalid") is None


def test_multi_get_list_with_string_key() -> None:
    """Test multi_get with string key on list (line 11)."""
    data = [1, 2, 3]
    # String key on list should return default
    assert multi_get(data, "key") is None
    assert multi_get(data, "key", default="error") == "error"


def test_multi_get_no_keys() -> None:
    """Test multi_get with no keys (line 18)."""
    data = {"a": 1}
    # No keys should return the original data
    assert multi_get(data) == {"a": 1}


def test_multi_get_complex_nested() -> None:
    """Test multi_get with complex nested structure."""
    data = {
        "level1": {
            "level2": [
                {"level3": {"value": 42}},
                {"level3": {"value": 84}},
            ],
        },
    }
    assert multi_get(data, "level1", "level2", 0, "level3", "value") == 42
    assert multi_get(data, "level1", "level2", 1, "level3", "value") == 84


def test_try_get_first_key_dict() -> None:
    """Test try_get_first_key with dictionary (line 23)."""
    data = {"a": 1, "b": 2, "c": 3}
    result = try_get_first_key(data)
    # Should return first key (order may vary in Python < 3.7)
    assert result in ["a", "b", "c"]


def test_try_get_first_key_empty_dict() -> None:
    """Test try_get_first_key with empty dictionary (line 24)."""
    assert try_get_first_key({}) is None


def test_try_get_first_key_with_default() -> None:
    """Test try_get_first_key with custom default (line 24)."""
    assert try_get_first_key({}, default="none") == "none"
    assert try_get_first_key({}, default=0) == 0


def test_try_get_first_key_type_error() -> None:
    """Test try_get_first_key with TypeError (line 24)."""
    # None should trigger TypeError
    assert try_get_first_key(None) is None
    assert try_get_first_key(None, default="error") == "error"

    # String is iterable, so it returns the first character
    # Let's test with an integer which is not iterable
    assert try_get_first_key(123) is None
    assert try_get_first_key(123, default="error") == "error"


def test_try_get_first_value_dict() -> None:
    """Test try_get_first_value with dictionary (line 30)."""
    data = {"a": 1, "b": 2, "c": 3}
    result = try_get_first_value(data)
    # Should return first value
    assert result in [1, 2, 3]


def test_try_get_first_value_empty_dict() -> None:
    """Test try_get_first_value with empty dictionary (line 31)."""
    assert try_get_first_value({}) is None


def test_try_get_first_value_with_default() -> None:
    """Test try_get_first_value with custom default (line 31)."""
    assert try_get_first_value({}, default="none") == "none"
    assert try_get_first_value({}, default=0) == 0


def test_try_get_first_value_type_error() -> None:
    """Test try_get_first_value with TypeError (line 31)."""
    # None should trigger TypeError
    assert try_get_first_value(None) is None
    assert try_get_first_value(None, default="error") == "error"

    # String should trigger TypeError
    assert try_get_first_value("string") is None
    assert try_get_first_value("string", default="error") == "error"


def test_try_get_first_value_attribute_error() -> None:
    """Test try_get_first_value with AttributeError (line 31)."""
    # Object without .values() method
    assert try_get_first_value([1, 2, 3]) is None
    assert try_get_first_value([1, 2, 3], default="error") == "error"


def test_move_to_dict_accepts_keyword_info_keys() -> None:
    data = {"author_name": "Ada", "author_id": "1", "message": "hello"}

    moved = move_to_dict(data, "author", info_keys=("author_name",))

    assert moved == {"name": "Ada"}
    assert data == {
        "author": {"name": "Ada"},
        "author_id": "1",
        "message": "hello",
    }


def test_move_to_dict_collects_and_strips_prefix() -> None:
    info = {"author_id": 1, "author_name": "x", "other": 2}
    sub = move_to_dict(info, "author")
    assert sub == {"id": 1, "name": "x"}
    assert info == {"other": 2, "author": {"id": 1, "name": "x"}}


def test_move_to_dict_drops_empty_values_keeps_falsey_scalars() -> None:
    info = {"a_id": None, "a_tags": [], "a_meta": {}, "a_n": 0, "a_s": ""}
    sub = move_to_dict(info, "a")
    assert sub == {"n": 0, "s": ""}


def test_move_to_dict_create_when_empty() -> None:
    info = {"unrelated": 1}
    move_to_dict(info, "author", create_when_empty=True)
    assert info["author"] == {}


def test_move_to_dict_merges_into_existing_subdict() -> None:
    info = {"author": {"id": 1}, "author_name": "x"}
    move_to_dict(info, "author")
    assert info["author"] == {"id": 1, "name": "x"}


def test_move_to_dict_replace_is_global_not_just_prefix() -> None:
    # Documents substring + replace-all behavior (potential gotcha): every
    # occurrence of "a_" is stripped, not just a leading prefix.
    info = {"a_foo_a_bar": "v"}
    sub = move_to_dict(info, "a")  # replace_key == "a_"
    assert sub == {"foo_bar": "v"}


def test_multi_get_bool_key_indexes_list_as_int() -> None:
    # bool is an int subclass, so a bool key indexes lists.
    assert multi_get([10, 20, 30], True) == 20  # True == 1
    assert multi_get([10, 20, 30], False) == 10  # False == 0

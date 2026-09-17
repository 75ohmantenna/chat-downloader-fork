# SPDX-License-Identifier: MIT
"""Dictionary traversal, fallback, and in-place grouping contracts."""

from __future__ import annotations

import pytest

from chat_downloader.utils.dict_utils import (
    move_to_dict,
    multi_get,
    try_get_first_key,
    try_get_first_value,
)


@pytest.mark.parametrize(
    ("data", "keys", "options", "expected"),
    [
        ({"a": 1, "b": 2, "c": 3}, ("a",), {}, 1),
        ({"a": 1, "b": 2, "c": 3}, ("b",), {}, 2),
        ({"user": {"name": "John"}}, ("user", "name"), {}, "John"),
        ({"user": {"details": {"age": 30}}}, ("user", "details", "age"), {}, 30),
        ({"a": 1, "b": 2}, ("c",), {}, None),
        ({"a": 1}, ("a", "nested"), {}, None),
        ({"a": 1}, ("b",), {"default": "N/A"}, "N/A"),
        ({"a": 1}, ("a", "nested"), {"default": 0}, 0),
        ([10, 20, 30], (5,), {}, None),
        ([10, 20, 30], (10,), {"default": "error"}, "error"),
        (
            {"items": [{"name": "item1"}, {"name": "item2"}]},
            ("items", 0, "name"),
            {},
            "item1",
        ),
        (
            {"items": [{"name": "item1"}, {"name": "item2"}]},
            ("items", 1, "name"),
            {},
            "item2",
        ),
        ("string", (0,), {}, None),
        ("string", (0,), {"default": "error"}, "error"),
        ({"items": [1, 2, 3]}, ("items", "invalid"), {}, None),
        ([1, 2, 3], ("key",), {}, None),
        ([1, 2, 3], ("key",), {"default": "error"}, "error"),
        ({"a": 1}, (), {}, {"a": 1}),
    ],
)
def test_multi_get(data, keys, options, expected):
    assert multi_get(data, *keys, **options) == expected


@pytest.mark.parametrize("sequence", [[10, 20, 30], (10, 20, 30)])
@pytest.mark.parametrize(
    ("index", "expected"), [(0, 10), (1, 20), (2, 30), (True, 20), (False, 10)]
)
def test_sequence_indices(sequence, index, expected):
    assert multi_get(sequence, index) == expected


@pytest.mark.parametrize(
    ("index", "key", "expected"), [(0, "a", 1), (1, "b", 2), (2, "c", 3)]
)
def test_list_of_dicts(index, key, expected):
    assert multi_get([{"a": 1}, {"b": 2}, {"c": 3}], index, key) == expected


@pytest.mark.parametrize(("index", "expected"), [(0, 42), (1, 84)])
def test_complex_nested(index, expected):
    data = {
        "level1": {
            "level2": [
                {"level3": {"value": 42}},
                {"level3": {"value": 84}},
            ]
        }
    }
    assert multi_get(data, "level1", "level2", index, "level3", "value") == expected


@pytest.mark.parametrize(
    ("function", "expected"), [(try_get_first_key, "a"), (try_get_first_value, 1)]
)
def test_first_entry(function, expected):
    assert function({"a": 1, "b": 2, "c": 3}) == expected


@pytest.mark.parametrize(
    ("function", "data"),
    [
        (try_get_first_key, {}),
        (try_get_first_key, None),
        (try_get_first_key, 123),
        (try_get_first_value, {}),
        (try_get_first_value, None),
        (try_get_first_value, "string"),
        (try_get_first_value, [1, 2, 3]),
    ],
)
@pytest.mark.parametrize(
    "options", [{}, {"default": "none"}, {"default": 0}, {"default": "error"}]
)
def test_first_entry_fallback(function, data, options):
    assert function(data, **options) == options.get("default")


@pytest.mark.parametrize(
    ("data", "key", "options", "sub", "expected"),
    [
        (
            {"author_name": "Ada", "author_id": "1", "message": "hello"},
            "author",
            {"info_keys": ("author_name",)},
            {"name": "Ada"},
            {"author": {"name": "Ada"}, "author_id": "1", "message": "hello"},
        ),
        (
            {"author_id": 1, "author_name": "x", "other": 2},
            "author",
            {},
            {"id": 1, "name": "x"},
            {"other": 2, "author": {"id": 1, "name": "x"}},
        ),
        (
            {"a_id": None, "a_tags": [], "a_meta": {}, "a_n": 0, "a_s": ""},
            "a",
            {},
            {"n": 0, "s": ""},
            {"a": {"n": 0, "s": ""}},
        ),
        (
            {"unrelated": 1},
            "author",
            {"create_when_empty": True},
            {},
            {"unrelated": 1, "author": {}},
        ),
        (
            {"author": {"id": 1}, "author_name": "x"},
            "author",
            {},
            {"name": "x"},
            {"author": {"id": 1, "name": "x"}},
        ),
        ({"a_foo_a_bar": "v"}, "a", {}, {"foo_bar": "v"}, {"a": {"foo_bar": "v"}}),
    ],
)
def test_move_to_dict(data, key, options, sub, expected):
    assert move_to_dict(data, key, **options) == sub
    assert data == expected

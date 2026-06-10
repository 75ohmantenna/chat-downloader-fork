# SPDX-License-Identifier: MIT

"""Unit tests for chat_downloader.utils.json_types accessors."""

from __future__ import annotations

import pytest

from chat_downloader.utils.json_types import (
    dig,
    get_bool,
    get_dict,
    get_float,
    get_int,
    get_list,
    get_str,
)

# ---------------------------------------------------------------------------
# get_str
# ---------------------------------------------------------------------------


def test_get_str_present() -> None:
    assert get_str({"k": "hello"}, "k") == "hello"


def test_get_str_missing_returns_default() -> None:
    assert get_str({}, "k") == ""


def test_get_str_missing_custom_default() -> None:
    assert get_str({}, "k", default="x") == "x"


def test_get_str_wrong_type_returns_default() -> None:
    assert get_str({"k": 42}, "k") == ""


def test_get_str_wrong_type_custom_default() -> None:
    assert get_str({"k": 42}, "k", default="x") == "x"


# ---------------------------------------------------------------------------
# get_int
# ---------------------------------------------------------------------------


def test_get_int_present() -> None:
    assert get_int({"k": 7}, "k") == 7


def test_get_int_missing_returns_default() -> None:
    assert get_int({}, "k") == 0


def test_get_int_missing_custom_default() -> None:
    assert get_int({}, "k", default=99) == 99


def test_get_int_wrong_type_returns_default() -> None:
    assert get_int({"k": "seven"}, "k") == 0


def test_get_int_bool_excluded() -> None:
    # bool is an int subclass but must not be treated as int
    assert get_int({"k": True}, "k") == 0


# ---------------------------------------------------------------------------
# get_float
# ---------------------------------------------------------------------------


def test_get_float_from_float() -> None:
    assert get_float({"k": 3.14}, "k") == pytest.approx(3.14)


def test_get_float_from_int() -> None:
    assert get_float({"k": 2}, "k") == pytest.approx(2.0)


def test_get_float_missing_returns_default() -> None:
    assert get_float({}, "k") == pytest.approx(0.0)


def test_get_float_missing_custom_default() -> None:
    assert get_float({}, "k", default=1.5) == pytest.approx(1.5)


def test_get_float_wrong_type_returns_default() -> None:
    assert get_float({"k": "x"}, "k") == pytest.approx(0.0)


def test_get_float_bool_excluded() -> None:
    assert get_float({"k": True}, "k") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_bool
# ---------------------------------------------------------------------------


def test_get_bool_true() -> None:
    assert get_bool({"k": True}, "k") is True


def test_get_bool_false() -> None:
    assert get_bool({"k": False}, "k") is False


def test_get_bool_missing_returns_default() -> None:
    assert get_bool({}, "k") is False


def test_get_bool_missing_custom_default() -> None:
    assert get_bool({}, "k", default=True) is True


def test_get_bool_wrong_type_returns_default() -> None:
    assert get_bool({"k": 1}, "k") is False


# ---------------------------------------------------------------------------
# get_dict
# ---------------------------------------------------------------------------


def test_get_dict_present() -> None:
    assert get_dict({"k": {"a": 1}}, "k") == {"a": 1}


def test_get_dict_missing_returns_empty() -> None:
    assert get_dict({}, "k") == {}


def test_get_dict_wrong_type_returns_empty() -> None:
    assert get_dict({"k": "not a dict"}, "k") == {}


# ---------------------------------------------------------------------------
# get_list
# ---------------------------------------------------------------------------


def test_get_list_present() -> None:
    assert get_list({"k": [1, 2]}, "k") == [1, 2]


def test_get_list_missing_returns_empty() -> None:
    assert get_list({}, "k") == []


def test_get_list_wrong_type_returns_empty() -> None:
    assert get_list({"k": "not a list"}, "k") == []


# ---------------------------------------------------------------------------
# dig
# ---------------------------------------------------------------------------


def test_dig_single_level() -> None:
    assert dig({"a": "v"}, "a") == "v"


def test_dig_nested() -> None:
    assert dig({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42


def test_dig_missing_key_returns_none() -> None:
    assert dig({"a": {}}, "a", "b") is None


def test_dig_non_dict_mid_path_returns_none() -> None:
    assert dig({"a": "leaf"}, "a", "b") is None


def test_dig_empty_path_returns_dict() -> None:
    d = {"x": 1}
    assert dig(d) == d


def test_dig_top_level_missing_key() -> None:
    assert dig({}, "a") is None

# SPDX-License-Identifier: MIT
"""String utility contracts, including empty and mixed-type inputs."""

from __future__ import annotations

import pytest

from chat_downloader.utils.string_utils import (
    camel_case_split,
    get_title_of_webpage,
    regex_search,
    remove_prefixes,
    remove_suffixes,
    replace_with_underscores,
    wrap_as_list,
)


@pytest.mark.parametrize(
    ("text", "pattern", "kwargs", "expected"),
    [
        ("test123abc", r"(\d+)", {}, "123"),
        ("test", r"(\d+)", {}, None),
        ("test", r"(\d+)", {"default": "none"}, "none"),
        ("test", r"(\d+)", {"default": 0}, 0),
        ("test123abc456", r"(\d+).*?(\d+)", {"group": 2}, "456"),
        ("test123abc", r"test(\d+)(\w+)", {"group": 2}, "abc"),
        ("test123abc", r"\d+", {"group": 0}, "123"),
    ],
)
def test_regex_search(text, pattern, kwargs, expected):
    assert regex_search(text, pattern, **kwargs) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<html><head><title>Test Page</title></head></html>", "Test Page"),
        ('<html><title class="main">My Title</title></html>', "My Title"),
        ("<html><head></head><body>Content</body></html>", None),
        ("<html><head><title>\nMultiline Title\n</title></head></html>", None),
    ],
)
def test_webpage_title(html, expected):
    assert get_title_of_webpage(html) == expected


@pytest.mark.parametrize("item", ["item", 42, None, {"a": 1}])
def test_wrap_scalar(item):
    assert wrap_as_list(item) == [item]


@pytest.mark.parametrize("items", [["a", "b", "c"], ("a", "b", "c")])
def test_wrap_preserves_sequence_identity(items):
    assert wrap_as_list(items) is items


@pytest.mark.parametrize(
    ("function", "text", "affixes", "expected"),
    [
        (remove_prefixes, "prefixtest", "prefix", "test"),
        (remove_prefixes, "test", "prefix", "test"),
        (remove_prefixes, "abctest", ["ab", "cd"], "ctest"),
        (remove_prefixes, "cdtest", ["ab", "cd"], "test"),
        (remove_prefixes, "test", ["pre", "fix"], "test"),
        (remove_prefixes, "test", [], "test"),
        (remove_prefixes, "abctest", ("ab", "cd"), "ctest"),
        (remove_suffixes, "testsuffix", "suffix", "test"),
        (remove_suffixes, "test", "suffix", "test"),
        (remove_suffixes, "testab", ["ab", "cd"], "test"),
        (remove_suffixes, "testcd", ["ab", "cd"], "test"),
        (remove_suffixes, "test", ["suf", "fix"], "test"),
        (remove_suffixes, "test", [], "test"),
        (remove_suffixes, "testab", ("ab", "cd"), "test"),
    ],
)
def test_remove_affixes(function, text, affixes, expected):
    assert function(text, affixes) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("camelCase", "camel_case"),
        ("PascalCase", "pascal_case"),
        ("HTTPResponse", "http_response"),
        ("XMLParser", "xml_parser"),
        ("URLPath", "url_path"),
        ("test", "test"),
        ("Test", "test"),
        ("lowercase", "lowercase"),
        ("getUserById", "get_user_by_id"),
        ("isValidHTTPRequest", "is_valid_http_request"),
    ],
)
def test_camel_case(text, expected):
    assert camel_case_split(text) == expected


def test_camel_case_numbers():
    assert "_" in camel_case_split("test123Data")


@pytest.mark.parametrize(
    ("text", "options", "expected"),
    [
        ("test-name", {}, "test_name"),
        ("my-var-name", {}, "my_var_name"),
        ("test.name", {"sep": "."}, "test_name"),
        ("test|name", {"sep": "|"}, "test_name"),
        ("test name", {"sep": " "}, "test_name"),
        ("testname", {}, "testname"),
        ("test_name", {}, "test_name"),
        ("a-b-c-d", {}, "a_b_c_d"),
        ("x.y.z", {"sep": "."}, "x_y_z"),
        ("", {}, ""),
    ],
)
def test_replace_separator(text, options, expected):
    assert replace_with_underscores(text, **options) == expected

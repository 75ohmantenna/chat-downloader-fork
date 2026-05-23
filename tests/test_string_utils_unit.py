# SPDX-License-Identifier: MIT

"""Comprehensive unit tests for string_utils module to improve coverage."""

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
    "text,pattern,kwargs,expected",
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
def test_regex_search(
    text: str, pattern: str, kwargs: dict, expected: object
) -> None:
    assert regex_search(text, pattern, **kwargs) == expected


def test_get_title_of_webpage_basic() -> None:
    """Test getting title from HTML (line 20)."""
    html = "<html><head><title>Test Page</title></head></html>"
    result = get_title_of_webpage(html)
    assert result == "Test Page"


def test_get_title_of_webpage_with_attributes() -> None:
    """Test title tag with attributes (line 20)."""
    html = '<html><title class="main">My Title</title></html>'
    result = get_title_of_webpage(html)
    assert result == "My Title"


def test_get_title_of_webpage_no_title() -> None:
    """Test HTML without title (line 20)."""
    html = "<html><head></head><body>Content</body></html>"
    result = get_title_of_webpage(html)
    assert result is None


def test_get_title_of_webpage_multiline() -> None:
    """Test title across multiple lines."""
    # The regex uses (.*?) which doesn't match newlines by default
    # So multiline titles won't match
    html = """<html>
    <head>
        <title>
            Multiline Title
        </title>
    </head>
    </html>"""
    result = get_title_of_webpage(html)
    # Should return None because regex doesn't match newlines
    assert result is None


def test_wrap_as_list_single_item() -> None:
    """Test wrapping single item (lines 31-32)."""
    assert wrap_as_list("item") == ["item"]
    assert wrap_as_list(42) == [42]
    assert wrap_as_list(None) == [None]


def test_wrap_as_list_already_list() -> None:
    """Test wrapping already-list (line 33)."""
    input_list = ["a", "b", "c"]
    result = wrap_as_list(input_list)
    assert result is input_list
    assert result == ["a", "b", "c"]


def test_wrap_as_list_tuple() -> None:
    """Test wrapping tuple (line 33)."""
    input_tuple = ("a", "b", "c")
    result = wrap_as_list(input_tuple)
    assert result is input_tuple
    assert result == ("a", "b", "c")


def test_wrap_as_list_dict() -> None:
    """Test wrapping dict (should wrap it)."""
    result = wrap_as_list({"a": 1})
    assert result == [{"a": 1}]


def test_remove_prefixes_single_prefix() -> None:
    """Test removing single prefix (lines 37-40)."""
    assert remove_prefixes("prefixtest", "prefix") == "test"
    assert remove_prefixes("test", "prefix") == "test"


def test_remove_prefixes_multiple_prefixes() -> None:
    """Test removing from list of prefixes (lines 37-40)."""
    assert remove_prefixes("abctest", ["ab", "cd"]) == "ctest"
    assert remove_prefixes("cdtest", ["ab", "cd"]) == "test"


def test_remove_prefixes_no_match() -> None:
    """Test remove_prefixes when no prefix matches (line 38-39)."""
    result = remove_prefixes("test", ["pre", "fix"])
    assert result == "test"


def test_remove_prefixes_empty_list() -> None:
    """Test remove_prefixes with empty list (line 37)."""
    result = remove_prefixes("test", [])
    assert result == "test"


def test_remove_prefixes_tuple() -> None:
    """Test remove_prefixes with tuple (line 37)."""
    result = remove_prefixes("abctest", ("ab", "cd"))
    assert result == "ctest"


def test_remove_suffixes_single_suffix() -> None:
    """Test removing single suffix (lines 44-47)."""
    assert remove_suffixes("testsuffix", "suffix") == "test"
    assert remove_suffixes("test", "suffix") == "test"


def test_remove_suffixes_multiple_suffixes() -> None:
    """Test removing from list of suffixes (lines 44-47)."""
    assert remove_suffixes("testab", ["ab", "cd"]) == "test"
    assert remove_suffixes("testcd", ["ab", "cd"]) == "test"


def test_remove_suffixes_no_match() -> None:
    """Test remove_suffixes when no suffix matches (line 45-46)."""
    result = remove_suffixes("test", ["suf", "fix"])
    assert result == "test"


def test_remove_suffixes_empty_list() -> None:
    """Test remove_suffixes with empty list (line 44)."""
    result = remove_suffixes("test", [])
    assert result == "test"


def test_remove_suffixes_tuple() -> None:
    """Test remove_suffixes with tuple (line 44)."""
    result = remove_suffixes("testab", ("ab", "cd"))
    assert result == "test"


def test_camel_case_split_basic() -> None:
    """Test camelCase splitting (line 51)."""
    assert camel_case_split("camelCase") == "camel_case"
    assert camel_case_split("PascalCase") == "pascal_case"


def test_camel_case_split_acronyms() -> None:
    """Test splitting with acronyms (line 51)."""
    assert camel_case_split("HTTPResponse") == "http_response"
    assert camel_case_split("XMLParser") == "xml_parser"
    assert camel_case_split("URLPath") == "url_path"


def test_camel_case_split_single_word() -> None:
    """Test splitting single word (line 51)."""
    assert camel_case_split("test") == "test"
    assert camel_case_split("Test") == "test"


def test_camel_case_split_lowercase() -> None:
    """Test splitting already lowercase (line 51)."""
    assert camel_case_split("lowercase") == "lowercase"


def test_camel_case_split_multiple_words() -> None:
    """Test splitting multiple words (line 51)."""
    assert camel_case_split("getUserById") == "get_user_by_id"
    assert camel_case_split("isValidHTTPRequest") == "is_valid_http_request"


def test_camel_case_split_numbers() -> None:
    """Test splitting with numbers."""
    # Numbers don't match the regex, so they're ignored
    result = camel_case_split("test123Data")
    assert "_" in result


def test_replace_with_underscores_default_sep() -> None:
    """Test replacing with underscores using default separator (line 55)."""
    assert replace_with_underscores("test-name") == "test_name"
    assert replace_with_underscores("my-var-name") == "my_var_name"


def test_replace_with_underscores_custom_sep() -> None:
    """Test replacing with custom separator (line 55)."""
    assert replace_with_underscores("test.name", sep=".") == "test_name"
    assert replace_with_underscores("test|name", sep="|") == "test_name"
    assert replace_with_underscores("test name", sep=" ") == "test_name"


def test_replace_with_underscores_no_separator() -> None:
    """Test replacing when separator not present (line 55)."""
    assert replace_with_underscores("testname") == "testname"
    assert replace_with_underscores("test_name") == "test_name"


def test_replace_with_underscores_multiple_occurrences() -> None:
    """Test replacing multiple occurrences (line 55)."""
    assert replace_with_underscores("a-b-c-d") == "a_b_c_d"
    assert replace_with_underscores("x.y.z", sep=".") == "x_y_z"


def test_replace_with_underscores_empty_string() -> None:
    """Test replacing empty string."""
    assert replace_with_underscores("") == ""

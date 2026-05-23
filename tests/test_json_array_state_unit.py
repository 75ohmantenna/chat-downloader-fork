# SPDX-License-Identifier: MIT

import io

from chat_downloader.output.json_array_state import (
    find_last_non_whitespace,
    find_previous_non_whitespace,
)


def test_find_last_non_whitespace_on_empty_stream() -> None:
    position, character = find_last_non_whitespace(io.StringIO(""))
    assert position is None
    assert character == ""


def test_find_last_non_whitespace_skips_trailing_whitespace() -> None:
    position, character = find_last_non_whitespace(io.StringIO("[1]\n  \t"))
    assert position == 2
    assert character == "]"


def test_find_previous_non_whitespace_skips_internal_whitespace() -> None:
    stream = io.StringIO("[  ]")
    position, character = find_previous_non_whitespace(stream, 2)
    assert position == 0
    assert character == "["


def test_find_previous_non_whitespace_before_start_returns_empty() -> None:
    position, character = find_previous_non_whitespace(io.StringIO("[]"), -1)
    assert position == 0
    assert character == ""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import InvalidParameter
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter

_MESSAGE_GROUPS = {
    "messages": ["text_message"],
    "superchat": ["paid_message", "paid_sticker", "membership_item"],
    "donations": ["donation_announcement"],
    "engagement": ["viewer_engagement_message"],
}

_FILTER_GROUPS = {
    "messages": ["text_message"],
    "superchat": ["paid_message", "paid_sticker"],
}


def _must_add(item, groups=(), types=()):
    return MessageFilter(_MESSAGE_GROUPS, groups, types).should_add(item)


# ---------------------------------------------------------------------------
# Message filtering (high-level via _must_add)
# ---------------------------------------------------------------------------


def test_must_add_item_with_message_types() -> None:
    item = {"message_type": "text_message"}
    assert _must_add(item, types=["text_message"])
    assert not _must_add(item, types=["paid_message"])


def test_must_add_item_with_message_groups() -> None:
    assert _must_add({"message_type": "text_message"}, groups=["messages"])
    assert _must_add({"message_type": "paid_message"}, groups=["superchat"])


def test_must_add_item_with_multiple_groups() -> None:
    groups = ["messages", "superchat"]
    assert _must_add({"message_type": "text_message"}, groups=groups)
    assert _must_add({"message_type": "paid_message"}, groups=groups)
    assert not _must_add(
        {"message_type": "donation_announcement"}, groups=groups
    )


def test_must_add_item_with_all_keyword() -> None:
    item = {"message_type": "any_type"}
    assert _must_add(item, groups=["all"])
    assert _must_add(item, types=["all"])


def test_message_types_and_groups_combined() -> None:
    assert _must_add(
        {"message_type": "paid_message"},
        groups=["messages"],
        types=["paid_message"],
    )
    assert _must_add(
        {"message_type": "text_message"},
        groups=["messages"],
        types=["paid_message"],
    )
    assert not _must_add(
        {"message_type": "donation_announcement"},
        groups=["messages"],
        types=["paid_message"],
    )


def test_must_add_item_with_unknown_group() -> None:
    with pytest.raises(InvalidParameter, match="Invalid groups specified"):
        _must_add({"message_type": "text_message"}, groups=["unknown_group"])


def test_must_add_item_with_multiple_types() -> None:
    types = ["text_message", "paid_message", "paid_sticker"]
    assert _must_add({"message_type": "text_message"}, types=types)
    assert _must_add({"message_type": "paid_sticker"}, types=types)
    assert not _must_add({"message_type": "donation_announcement"}, types=types)


def test_must_add_item_with_empty_filters() -> None:
    assert _must_add({"message_type": "text_message"})


def test_must_add_item_missing_message_type() -> None:
    assert not _must_add({"author": "test"}, groups=["messages"])


def test_must_add_item_none_message_type() -> None:
    assert not _must_add({"message_type": None}, groups=["messages"])


@pytest.mark.parametrize(
    "msg_type", ["paid_message", "paid_sticker", "membership_item"]
)
def test_superchat_group_includes_multiple_types(msg_type: str) -> None:
    assert _must_add({"message_type": msg_type}, groups=["superchat"])


@pytest.mark.parametrize(
    ("message_type", "should_add"),
    [
        ("text_message", True),
        ("paid_message", True),
        ("paid_sticker", True),
        ("membership_item", True),
        ("donation_announcement", False),
        ("viewer_engagement_message", False),
        ("unknown_type", False),
    ],
)
def test_complex_filtering_scenario(
    message_type: str, should_add: bool
) -> None:
    result = _must_add(
        {"message_type": message_type}, groups=["messages", "superchat"]
    )
    assert result == should_add


# ---------------------------------------------------------------------------
# MessageFilter direct tests
# ---------------------------------------------------------------------------


def test_no_filters_accepts_all() -> None:
    f = MessageFilter(_FILTER_GROUPS)
    assert f.should_add({"message_type": "anything"})


def test_empty_lists_accepts_all() -> None:
    f = MessageFilter(_FILTER_GROUPS, [], [])
    assert f.should_add({"message_type": "anything"})


def test_filter_by_types() -> None:
    f = MessageFilter(_FILTER_GROUPS, types_to_add=["text_message"])
    assert f.should_add({"message_type": "text_message"})
    assert not f.should_add({"message_type": "paid_message"})


def test_filter_by_groups() -> None:
    f = MessageFilter(_FILTER_GROUPS, groups_to_add=["superchat"])
    assert f.should_add({"message_type": "paid_message"})
    assert f.should_add({"message_type": "paid_sticker"})
    assert not f.should_add({"message_type": "text_message"})


def test_types_and_groups_are_additive() -> None:
    f = MessageFilter(
        _FILTER_GROUPS,
        groups_to_add=["messages"],
        types_to_add=["paid_message"],
    )
    assert f.should_add({"message_type": "text_message"})
    assert f.should_add({"message_type": "paid_message"})
    assert not f.should_add({"message_type": "paid_sticker"})


def test_all_keyword_in_groups() -> None:
    f = MessageFilter(_FILTER_GROUPS, groups_to_add=["all"])
    assert f.should_add({"message_type": "anything"})


def test_all_keyword_in_types() -> None:
    f = MessageFilter(_FILTER_GROUPS, types_to_add=["all"])
    assert f.should_add({"message_type": "anything"})


def test_missing_message_type_in_filter() -> None:
    f = MessageFilter(_FILTER_GROUPS, groups_to_add=["messages"])
    assert not f.should_add({"author": "test"})


# ---------------------------------------------------------------------------
# TimeRangeFilter
# ---------------------------------------------------------------------------


def test_no_range_accepts_all() -> None:
    f = TimeRangeFilter()
    assert f.check({"time_in_seconds": 100}) == "yield"


def test_before_start_stops() -> None:
    f = TimeRangeFilter(start_time=10)
    assert f.check({"time_in_seconds": 5}) == "stop"


def test_after_end_stops() -> None:
    f = TimeRangeFilter(end_time=10)
    assert f.check({"time_in_seconds": 15}) == "stop"


def test_in_range_yields() -> None:
    f = TimeRangeFilter(start_time=5, end_time=15)
    assert f.check({"time_in_seconds": 10}) == "yield"


def test_offset_applied() -> None:
    f = TimeRangeFilter(start_time=10, offset=5)
    assert f.check({"time_in_seconds": 3}) == "stop"
    assert f.check({"time_in_seconds": 6}) == "yield"


def test_always_skip_mode() -> None:
    f = TimeRangeFilter(start_time=10, end_time=20, skip_mode="always")
    assert f.check({"time_in_seconds": 5}) == "skip"
    assert f.check({"time_in_seconds": 3}) == "skip"
    assert f.check({"time_in_seconds": 12}) == "yield"
    assert f.check({"time_in_seconds": 7}) == "skip"
    assert f.check({"time_in_seconds": 25}) == "stop"


def test_first_page_skip_mode() -> None:
    f = TimeRangeFilter(start_time=10, skip_mode="first_page")
    assert f.check({"time_in_seconds": 5}) == "skip"
    assert f.check({"time_in_seconds": 3}) == "skip"
    f.end_page()
    assert f.check({"time_in_seconds": 5}) == "stop"


def test_first_page_in_range_then_next_page() -> None:
    f = TimeRangeFilter(start_time=10, skip_mode="first_page")
    assert f.check({"time_in_seconds": 5}) == "skip"
    assert f.check({"time_in_seconds": 12}) == "yield"
    f.end_page()
    assert f.check({"time_in_seconds": 5}) == "stop"


def test_missing_time_defaults_to_zero() -> None:
    f = TimeRangeFilter(start_time=10)
    assert f.check({}) == "stop"


def test_missing_time_no_range() -> None:
    f = TimeRangeFilter()
    assert f.check({}) == "yield"

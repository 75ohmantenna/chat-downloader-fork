# SPDX-License-Identifier: MIT

"""Comprehensive unit tests for time_utils module to improve coverage."""

from __future__ import annotations

import datetime

import pytest

from chat_downloader.utils.time_utils import (
    MICROSECONDS_PER_SECOND,
    UTC,
    ensure_seconds,
    microseconds_to_timestamp,
    parse_date,
    parse_iso8601,
    parse_timezone,
    seconds_to_time,
    time_to_seconds,
    timestamp_to_microseconds,
)


def test_timestamp_to_microseconds_basic() -> None:
    """Test basic RFC3339 timestamp conversion."""
    result = timestamp_to_microseconds("2020-01-01T00:00:00.000Z")
    assert isinstance(result, int)
    assert result > 0


def test_timestamp_to_microseconds_with_fractional_seconds() -> None:
    """Test timestamp with fractional seconds."""
    # Test with different fractional precision
    ts1 = timestamp_to_microseconds("2020-01-01T00:00:00.123Z")
    ts2 = timestamp_to_microseconds("2020-01-01T00:00:00.123456Z")
    ts3 = timestamp_to_microseconds("2020-01-01T00:00:00.123456789Z")

    # All should be valid
    assert isinstance(ts1, int)
    assert isinstance(ts2, int)
    assert isinstance(ts3, int)

    # More precision should give larger values
    assert ts2 > ts1


def test_timestamp_to_microseconds_without_fractional_part() -> None:
    """Test timestamp without fractional seconds (lines 70-83)."""
    # Timestamp without fractional part should work
    result = timestamp_to_microseconds("2020-06-15T12:30:45Z")
    assert isinstance(result, int)
    assert result > 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        (None, 0),
        ("-1:30", -90),
        ("-5:00", -300),
        ("-1:00:00", -3600),
        ("1,30", 130),
        ("2,30,45", 23045),
        ("45", 45),
        ("5:30", 330),
        ("2:15:30", 8130),
        ("12:30:45", 45045),
    ],
)
def test_time_to_seconds(text: str | None, expected: int) -> None:
    assert time_to_seconds(text) == expected


def test_timestamp_to_microseconds_comma_decimal_matches_dot() -> None:
    """Comma is an RFC3339-valid fractional separator and must match the dot."""
    assert timestamp_to_microseconds(
        "2020-01-01T00:00:00,123Z"
    ) == timestamp_to_microseconds("2020-01-01T00:00:00.123Z")


def test_timestamp_to_microseconds_utc_offset_equals_z() -> None:
    """A +00:00 offset is the same instant as Z."""
    assert timestamp_to_microseconds(
        "2020-01-01T00:00:00+00:00"
    ) == timestamp_to_microseconds("2020-01-01T00:00:00Z")


def test_timestamp_to_microseconds_positive_offset_shifts_epoch() -> None:
    """+01:00 means the same wall clock is one hour earlier in UTC."""
    z = timestamp_to_microseconds("2020-01-01T01:00:00Z")
    plus1 = timestamp_to_microseconds("2020-01-01T01:00:00+01:00")
    assert z - plus1 == 3600 * MICROSECONDS_PER_SECOND


def test_seconds_to_time_negative() -> None:
    """Test negative seconds conversion (lines 132-137)."""
    assert seconds_to_time(-90) == "-1:30"
    assert seconds_to_time(-3600) == "-1:00:00"
    assert seconds_to_time(-5) == "-0:05"


def test_seconds_to_time_custom_format() -> None:
    """Test custom format string (lines 133-137)."""
    # Custom format
    result = seconds_to_time(3665, format="{:02}h {:02}m {:02}s")
    assert "h" in result
    assert "m" in result
    assert "s" in result


def test_seconds_to_time_no_remove_leading_zeroes() -> None:
    """Test without removing leading zeroes (lines 135-137)."""
    assert seconds_to_time(5, remove_leading_zeroes=False) == "0:00:05"
    assert seconds_to_time(65, remove_leading_zeroes=False) == "0:01:05"
    assert seconds_to_time(3665, remove_leading_zeroes=False) == "1:01:05"


def test_seconds_to_time_float_input() -> None:
    """Test with float input."""
    assert seconds_to_time(90.5) == "1:30"
    assert seconds_to_time(3600.9) == "1:00:00"


def test_ensure_seconds_with_none() -> None:
    """Test ensure_seconds with None (lines 169-170)."""
    assert ensure_seconds(None) is None
    assert ensure_seconds(None, default=0) == 0
    assert ensure_seconds(None, default="default") == "default"


def test_ensure_seconds_with_float() -> None:
    """Test ensure_seconds with float (lines 172-173)."""
    assert ensure_seconds(60) == 60.0
    assert ensure_seconds(90.5) == 90.5
    assert ensure_seconds("120") == 120.0


def test_ensure_seconds_with_string_time() -> None:
    """Test ensure_seconds with time string (lines 174-178)."""
    assert ensure_seconds("1:30") == 90
    assert ensure_seconds("2:00:00") == 7200


def test_ensure_seconds_with_invalid_input() -> None:
    """Test ensure_seconds with invalid input (lines 174-180)."""
    # TypeError/AttributeError cases
    result = ensure_seconds(object(), default=123)
    assert result == 123

    # List input should trigger TypeError
    result = ensure_seconds([1, 2, 3], default=456)
    assert result == 456

    assert ensure_seconds("not-a-time", default=789) == 789
    assert ensure_seconds("1:not-a-number", default=789) == 789
    assert ensure_seconds(float("nan"), default=789) == 789
    assert ensure_seconds(float("inf"), default=789) == 789
    assert ensure_seconds(10**10_000, default=789) == 789


def test_ensure_seconds_non_string_value_error() -> None:
    """Test ensure_seconds line 178: ValueError but value is not a string.

    float() raises ValueError for custom objects with a __float__ that raises.
    Since the value is not a string, the except ValueError branch hits
    'return default' (line 178) rather than calling time_to_seconds.
    """

    class _RaisesValueError:
        def __float__(self) -> float:
            msg = "intentional"
            raise ValueError(msg)

    result = ensure_seconds(_RaisesValueError(), default=99)
    assert result == 99


def test_parse_timezone_utc() -> None:
    """Test parsing UTC timezone (lines 185-187)."""
    matches = {"timezone": "Z"}
    tz = parse_timezone(matches)
    assert tz == UTC
    assert tz == datetime.UTC


def test_parse_timezone_none() -> None:
    """Test parsing no timezone (lines 189-190)."""
    matches = {}
    tz = parse_timezone(matches)
    assert tz == UTC

    # Custom default
    custom_tz = datetime.timezone(datetime.timedelta(hours=5))
    tz = parse_timezone(matches, default_timezone=custom_tz)
    assert tz == custom_tz


def test_parse_timezone_positive_offset() -> None:
    """Test parsing positive timezone offset (lines 191-200)."""
    matches = {
        "timezone": "+05:30",
        "tz_sign": "+",
        "tz_hour": "5",
        "tz_minute": "30",
    }
    tz = parse_timezone(matches)
    assert tz.utcoffset(None) == datetime.timedelta(hours=5, minutes=30)


def test_parse_timezone_negative_offset() -> None:
    """Test parsing negative timezone offset (lines 195-198)."""
    matches = {
        "timezone": "-08:00",
        "tz_sign": "-",
        "tz_hour": "8",
        "tz_minute": "0",
    }
    tz = parse_timezone(matches)
    assert tz.utcoffset(None) == datetime.timedelta(hours=-8)


def test_parse_timezone_without_minutes() -> None:
    """Test parsing timezone without minutes."""
    matches = {"timezone": "+05", "tz_sign": "+", "tz_hour": "5"}
    tz = parse_timezone(matches)
    assert tz.utcoffset(None) == datetime.timedelta(hours=5)


def test_parse_date_basic() -> None:
    """Test basic date parsing (lines 217-241)."""
    dt = parse_date("2020-01-15T10:30:45Z")
    assert dt.year == 2020
    assert dt.month == 1
    assert dt.day == 15
    assert dt.hour == 10
    assert dt.minute == 30
    assert dt.second == 45


def test_parse_date_with_fractional_seconds() -> None:
    """Test parsing date with fractional seconds (lines 235-237)."""
    dt = parse_date("2020-06-15T12:30:45.123456Z")
    assert dt.microsecond == 123456


def test_parse_date_with_timezone_offset() -> None:
    """Test parsing date with timezone offset (lines 238)."""
    dt = parse_date("2020-06-15T12:30:45+05:30")
    assert dt.tzinfo.utcoffset(None) == datetime.timedelta(hours=5, minutes=30)

    dt_neg = parse_date("2020-06-15T12:30:45-08:00")
    assert dt_neg.tzinfo.utcoffset(None) == datetime.timedelta(hours=-8)


def test_parse_date_year_only() -> None:
    """Test parsing year only."""
    dt = parse_date("2020")
    assert dt.year == 2020
    assert dt.month == 1
    assert dt.day == 1


def test_parse_date_year_month() -> None:
    """Test parsing year-month."""
    dt = parse_date("2020-06")
    assert dt.year == 2020
    assert dt.month == 6
    assert dt.day == 1


def test_parse_date_with_dashes() -> None:
    """Test parsing date with dashes (monthdash, daydash)."""
    dt = parse_date("2020-6-5")
    assert dt.year == 2020
    assert dt.month == 6
    assert dt.day == 5


def test_parse_date_compact_format() -> None:
    """Test parsing compact format (no dashes)."""
    dt = parse_date("20200615T123045Z")
    assert dt.year == 2020
    assert dt.month == 6
    assert dt.day == 15
    assert dt.hour == 12
    assert dt.minute == 30
    assert dt.second == 45


def test_parse_date_invalid_string() -> None:
    """Test parsing invalid date string (lines 222-223)."""
    with pytest.raises(ValueError):
        parse_date("not a date")

    with pytest.raises(ValueError):
        parse_date("2020-13-01")  # Invalid month


def test_parse_date_type_error() -> None:
    """Test parsing with TypeError (lines 219-220)."""
    with pytest.raises(ValueError):
        parse_date(None)

    with pytest.raises(ValueError):
        parse_date(12345)


def test_parse_date_overflow_error() -> None:
    """Test parsing with overflow (lines 240-241)."""
    # Invalid date that would cause overflow
    with pytest.raises(ValueError):
        parse_date("2020-02-30")  # Invalid day for February


def test_parse_iso8601() -> None:
    """Test ISO8601 parsing to microseconds (line 245)."""
    result = parse_iso8601("2020-01-01T00:00:00Z")
    assert isinstance(result, float)
    assert result > 0

    # Verify it returns microseconds
    result2 = parse_iso8601("2020-01-01T00:00:01Z")
    assert result2 - result == MICROSECONDS_PER_SECOND


def test_microseconds_to_timestamp_custom_format() -> None:
    """Test custom format in microseconds_to_timestamp."""
    microseconds = 1577836800000000  # 2020-01-01 00:00:00 UTC

    # Default format
    result1 = microseconds_to_timestamp(microseconds)
    assert "2020" in result1

    # Custom format
    result2 = microseconds_to_timestamp(microseconds, format="%Y/%m/%d")
    assert result2 == "2020/01/01"

    # Another format
    result3 = microseconds_to_timestamp(microseconds, format="%H:%M:%S")
    assert ":" in result3


def test_microseconds_to_timestamp_with_float() -> None:
    """Test microseconds_to_timestamp with float input."""
    microseconds = 1577836800000000.5
    result = microseconds_to_timestamp(microseconds)
    assert isinstance(result, str)
    assert "2020" in result

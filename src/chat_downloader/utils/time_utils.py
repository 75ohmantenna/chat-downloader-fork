# SPDX-License-Identifier: MIT

"""Time parsing, formatting, and conversion utilities."""

from __future__ import annotations

import datetime
import re
from typing import Any

# Time conversion constants
MICROSECONDS_PER_SECOND = 1_000_000
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600

# Adapted from https://github.com/micktwomey/pyiso8601/
ISO8601_REGEX = re.compile(
    r"""
    (?P<year>[0-9]{4})
    (
        (
            (-(?P<monthdash>[0-9]{1,2}))
            |
            (?P<month>[0-9]{2})
            (?!$)  # Don't allow YYYYMM
        )
        (
            (
                (-(?P<daydash>[0-9]{1,2}))
                |
                (?P<day>[0-9]{2})
            )
            (
                (
                    (?P<separator>[ T])
                    (?P<hour>[0-9]{2})
                    (:{0,1}(?P<minute>[0-9]{2})){0,1}
                    (
                        :{0,1}(?P<second>[0-9]{1,2})
                        ([.,](?P<second_fraction>[0-9]+)){0,1}
                    ){0,1}
                    (?P<timezone>
                        Z
                        |
                        (
                            (?P<tz_sign>[-+])
                            (?P<tz_hour>[0-9]{2})
                            :{0,1}
                            (?P<tz_minute>[0-9]{2}){0,1}
                        )
                    ){0,1}
                ){0,1}
            )
        ){0,1}  # YYYY-MM
    ){0,1}  # YYYY only
    $
    """,
    re.VERBOSE,
)

UTC = datetime.UTC


def timestamp_to_microseconds(timestamp: str) -> int:
    """Convert an RFC3339 timestamp to microseconds since the Unix epoch.

    Delegates to :func:`parse_iso8601`, which handles ``Z``, ``+hh:mm``/
    ``-hh:mm`` offsets, and both ``.``/``,`` fractional-second separators.

    Args:
        timestamp: RFC3339 timestamp string (e.g.
            ``"2024-01-01T00:00:00.123Z"``).

    Returns:
        Number of microseconds since the Unix epoch.
    """
    return round(parse_iso8601(timestamp))


def time_to_seconds(time: str) -> int:
    """Convert timestamp string of the form 'hh:mm:ss' to seconds.

    :param time: Timestamp of the form 'hh:mm:ss'
    :type time: str
    :return: The corresponding number of seconds
    :rtype: int
    """
    if not time:
        return 0

    # Remove commas and check for negative sign
    is_negative = time[0] == "-"
    clean_time = time.replace(",", "")

    # Split into parts and reverse (seconds, minutes, hours, ...)
    parts = clean_time.split(":")
    reversed_parts = reversed(parts)

    # Calculate total seconds: seconds + (minutes * 60) + (hours * 3600)
    total: int = sum(
        abs(int(part)) * (SECONDS_PER_MINUTE**i)
        for i, part in enumerate(reversed_parts)
    )

    return -total if is_negative else total


def seconds_to_time(
    seconds: float,
    format: str = "{}:{:02}:{:02}",  # noqa: A002 — public API parameter; callers pass format= by name
    remove_leading_zeroes: bool = True,
) -> str:
    """Convert seconds to timestamp.

    :param seconds: Number of seconds
    :type seconds: int
    :param format: The format string with elements representing hours, minutes
        and seconds. Defaults to '{}:{:02}:{:02}'
    :type format: str, optional
    :param remove_leading_zeroes: Whether to remove leading zeroes when seconds
        > 60, defaults to True
    :type remove_leading_zeroes: bool, optional
    :return: The corresponding timestamp string
    :rtype: str
    """
    h, remainder = divmod(abs(int(seconds)), SECONDS_PER_HOUR)
    m, s = divmod(remainder, SECONDS_PER_MINUTE)
    time_string = format.format(h, m, s)
    return ("-" if seconds < 0 else "") + (
        re.sub(r"^0:0?", "", time_string)
        if remove_leading_zeroes
        else time_string
    )


def microseconds_to_timestamp(
    microseconds: float,
    format: str = "%Y-%m-%d %H:%M:%S",  # noqa: A002 — public API parameter; callers pass format= by name
) -> str:
    """Convert unix time to human-readable timestamp.

    :param microseconds: UNIX microseconds
    :type microseconds: float
    :param format: The format string, defaults to '%Y-%m-%d %H:%M:%S'. For
        information on supported codes, see https://strftime.org/ and
        https://docs.python.org/3/library/datetime.html#strftime-and-strptime-
        format-codes
    :type format: str, optional
    :return: Human readable timestamp corresponding to the format
    :rtype: str
    """
    return datetime.datetime.fromtimestamp(
        microseconds // MICROSECONDS_PER_SECOND,
        tz=UTC,
    ).strftime(format)


def ensure_seconds(
    time: float | str | None, default: Any = None
) -> float | Any:
    """Ensure time is returned in seconds.

    :param time: The time, in seconds or 'hh:mm:ss'.
    :type time: float | str
    :param default: Returns this if unable to parse the time, defaults to None
    :type default: object, optional
    :return: The corresponding number of seconds
    :rtype: float
    """
    if time is None:
        return default

    try:
        return float(time)
    except ValueError:
        # If float conversion fails, time must be a string timestamp
        if isinstance(time, str):
            return time_to_seconds(time)
        return default
    except (TypeError, AttributeError):
        return default


def parse_timezone(
    matches: dict[str, str],
    default_timezone: datetime.tzinfo | None = UTC,
) -> datetime.tzinfo | None:
    """Parses ISO 8601 time zone specs into tzinfo offsets."""
    tz = matches.get("timezone")
    if tz == "Z":
        return UTC

    if tz is None:
        return default_timezone
    sign = matches.get("tz_sign")
    hours = int(matches.get("tz_hour", 0))
    minutes = int(matches.get("tz_minute", 0))
    description = f"{sign}{hours:02d}:{minutes:02d}"
    if sign == "-":
        hours = -hours
        minutes = -minutes
    return datetime.timezone(
        datetime.timedelta(hours=hours, minutes=minutes),
        description,
    )


def parse_date(
    datestring: str,
    default_timezone: datetime.tzinfo | None = UTC,
) -> datetime.datetime:
    """Parse an ISO 8601 date string into a datetime object.

    The timezone is parsed from the date string.  It is common to receive
    dates without a timezone (not strictly correct); in that case the
    ``default_timezone`` is applied (UTC by default).

    Args:
        datestring: The date string to parse.
        default_timezone: A ``datetime.tzinfo`` instance used when no timezone
            is present in ``datestring``.  Pass ``None`` to return a naive
            datetime.

    Returns:
        A :class:`datetime.datetime` instance.

    Raises:
        ValueError: When the date string cannot be parsed or the datetime
            object cannot be constructed.
    """
    try:
        m = ISO8601_REGEX.match(datestring)
    except TypeError as e:
        msg = f"Expected a string date, got {type(datestring).__name__!r}"
        raise ValueError(msg) from e

    if not m:
        msg = f"Unable to parse date string {datestring!r}"
        raise ValueError(msg)

    groups = {k: v for k, v in m.groupdict().items() if v is not None}

    try:
        return datetime.datetime(
            year=int(groups.get("year", 0)),
            month=int(groups.get("month", groups.get("monthdash", 1))),
            day=int(groups.get("day", groups.get("daydash", 1))),
            hour=int(groups.get("hour", 0)),
            minute=int(groups.get("minute", 0)),
            second=int(groups.get("second", 0)),
            microsecond=int(
                float(f"0.{groups.get('second_fraction', 0)}")
                * MICROSECONDS_PER_SECOND,
            ),
            tzinfo=parse_timezone(groups, default_timezone=default_timezone),
        )
    except (ValueError, OverflowError) as e:
        msg = f"Date components out of range in {datestring!r}: {e}"
        raise ValueError(msg) from e


def parse_iso8601(data_str: str) -> float:
    """Parse an ISO 8601 string and return microseconds since the Unix epoch."""
    return parse_date(data_str).timestamp() * MICROSECONDS_PER_SECOND

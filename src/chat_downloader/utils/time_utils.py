# SPDX-License-Identifier: MIT

"""Time parsing, formatting, and conversion utilities."""

from __future__ import annotations

import datetime
import math
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
    """Convert RFC3339 to Unix-epoch microseconds via :func:`parse_iso8601`.

    Supports Z, +hh:mm/-hh:mm offsets, and . or , fractional separators,
    e.g. ``2024-01-01T00:00:00.123Z``.
    """
    return round(parse_iso8601(timestamp))


def time_to_seconds(time: str) -> int:
    """Convert an 'hh:mm:ss' timestamp to seconds."""
    if not time:
        return 0

    is_negative = time[0] == "-"
    clean_time = time.replace(",", "")

    parts = clean_time.split(":")
    reversed_parts = reversed(parts)

    total: int = sum(
        abs(int(part)) * (SECONDS_PER_MINUTE**i)
        for i, part in enumerate(reversed_parts)
    )

    return -total if is_negative else total


def seconds_to_time(
    seconds: float,
    *,
    format: str = "{}:{:02}:{:02}",  # noqa: A002 — public API parameter; callers pass format= by name
    remove_leading_zeroes: bool = True,
) -> str:
    """Convert seconds to a timestamp.

    Args:
        seconds: Seconds to convert (may be negative).
        format: Format string with hours, minutes, and seconds elements.
        remove_leading_zeroes: Remove leading zeroes when seconds > 60.
    """
    h, remainder = divmod(abs(int(seconds)), SECONDS_PER_HOUR)
    m, s = divmod(remainder, SECONDS_PER_MINUTE)
    time_string = format.format(h, m, s)
    return ("-" if seconds < 0 else "") + (
        re.sub(r"^0:0?", "", time_string) if remove_leading_zeroes else time_string
    )


def microseconds_to_timestamp(
    microseconds: float,
    format: str = "%Y-%m-%d %H:%M:%S",  # noqa: A002 — public API parameter; callers pass format= by name
) -> str:
    """Convert Unix microseconds to a human-readable timestamp.

    Args:
        microseconds: Unix time in microseconds to convert.
        format: strftime format; codes: https://strftime.org/ and
            https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes.
    """
    return datetime.datetime.fromtimestamp(
        microseconds // MICROSECONDS_PER_SECOND,
        tz=UTC,
    ).strftime(format)


def ensure_seconds(time: float | str | None, default: Any = None) -> float | Any:
    """Return time in seconds, or default if it cannot be parsed.

    Args:
        time: Seconds or 'hh:mm:ss'.
        default: Value returned when time is None or unparsable.
    """
    if time is None:
        return default

    try:
        seconds = float(time)
    except ValueError:
        # If float conversion fails, time must be a string timestamp
        if isinstance(time, str):
            try:
                return time_to_seconds(time)
            except (TypeError, ValueError):
                return default
        return default
    except (TypeError, AttributeError, OverflowError):
        return default
    return seconds if math.isfinite(seconds) else default


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
    """Parse an ISO 8601 date string, using its timezone when present.

    Args:
        datestring: ISO 8601 date string to parse.
        default_timezone: Used for commonly received timezone-less dates
            (not strictly correct ISO 8601); defaults to UTC. None returns
            a naive datetime.

    Raises:
        ValueError: Date parsing or datetime construction fails.
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

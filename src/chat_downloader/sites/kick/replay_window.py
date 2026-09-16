# SPDX-License-Identifier: MIT

"""Pure replay window selection and boundary classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.errors import ParsingError
from chat_downloader.utils.time_utils import ensure_seconds

from .parsing.messages import parse_chat_message

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict


def _apply_request_window(
    vod_start_dt: datetime,
    vod_end_dt: datetime,
    request: ChatRequest,
) -> tuple[datetime, datetime]:
    """Apply request-relative offsets to a VOD's absolute time window."""
    duration = max(0.0, (vod_end_dt - vod_start_dt).total_seconds())
    start_offset = cast("float", ensure_seconds(request.start_time, 0.0))
    end_offset = cast("float", ensure_seconds(request.end_time, duration))

    bounded_start = min(max(start_offset, 0.0), duration)
    bounded_end = min(max(end_offset, 0.0), duration)
    return (
        vod_start_dt + timedelta(seconds=bounded_start),
        vod_start_dt + timedelta(seconds=bounded_end),
    )


def _classify_message(
    raw: JSONDict,
    start_dt: datetime,
    end_dt: datetime,
    diagnostics: dict[str, object] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Classify one newest-first record against the selected replay window."""
    state = diagnostics if diagnostics is not None else {}
    reason = "malformed_timestamp"
    created_raw = raw.get("created_at", "")
    if not isinstance(created_raw, str):
        state[reason] = cast("int", state.get(reason, 0)) + 1
        return None, False
    try:
        msg_dt = datetime.fromisoformat(created_raw)
    except (ValueError, TypeError):
        state[reason] = cast("int", state.get(reason, 0)) + 1
        return None, False

    if msg_dt.tzinfo is None:
        msg_dt = msg_dt.replace(tzinfo=UTC)

    if msg_dt < start_dt:
        state["before_start"] = cast("int", state.get("before_start", 0)) + 1
        return None, True
    if msg_dt > end_dt:
        state["after_end"] = cast("int", state.get("after_end", 0)) + 1
        return None, False

    try:
        parsed = parse_chat_message(raw)
    except ParsingError:
        state["parse_error"] = cast("int", state.get("parse_error", 0)) + 1
        return None, False
    return parsed, False


def _cursor_after(timestamp: datetime) -> str:
    """Return a reverse cursor after the inclusive, second-granular end."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = timestamp - epoch
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
        + 1_000_000
    )
    return str(microseconds)

# SPDX-License-Identifier: MIT

"""Content-free inspection of YouTube JSONL records and run provenance."""

from __future__ import annotations

import math
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.utils.capture_reader import read_capture_lines
from chat_downloader.utils.json_types import get_dict, get_str

from .constants_message import _MESSAGE_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping

_KNOWN_TYPES = frozenset(_MESSAGE_TYPES) - {"all"} | {"chat_ended"}
_PROFILES = {"youtube_web", "youtube_android", "youtube_ios", "twitch_web"}
_VIEWS = {"Top chat", "Live chat", "Top chat replay", "Live chat replay"}
_INVALID_SUMMARY = "invalid_run_summary"
_COUNTERS = (
    "bootstrap_request_count",
    "bootstrap_http_error_count",
    "bootstrap_network_error_count",
    "bootstrap_fallback_count",
    "bootstrap_profile_switch_count",
    "continuation_request_count",
    "continuation_retry_count",
    "continuation_profile_switch_count",
    "http_error_count",
    "network_error_count",
    "json_error_count",
    "parse_error",
)


def _offset(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except OverflowError:
        return None
    return numeric if math.isfinite(numeric) else None


def _choice(value: object, choices: set[str]) -> str | None:
    return value if isinstance(value, str) and value in choices else None


class _Inspection:
    """Keep aggregate counts in memory and text-message IDs in temporary storage."""

    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database
        database.execute("CREATE TABLE ids (id TEXT PRIMARY KEY) WITHOUT ROWID")
        self.lines = 0
        self.records = 0
        self.types = dict.fromkeys(sorted(_KNOWN_TYPES), 0)
        self.issues: dict[str, dict[str, int]] = {}
        self.missing_timestamps = 0
        self.zero_without_timestamp = 0
        self.minimum_offset: float | None = None
        self.maximum_offset: float | None = None
        self.previous_offset: float | None = None
        self.offset_backsteps = 0

    def issue(self, key: str) -> None:
        entry = self.issues.setdefault(key, {"count": 0, "first_line": self.lines})
        entry["count"] += 1

    def record(self, record: Mapping[str, object]) -> None:
        self.records += 1
        kind = get_str(record, "message_type")
        if kind in self.types:
            self.types[kind] += 1
        else:
            self.issue("unknown_message_type")
        if kind == "text_message" and get_str(record, "action_type") in {
            "",
            "add_chat_item",
        }:
            identity = get_str(record, "message_id")
            if not identity:
                self.issue("missing_text_message_id")
            elif (
                self.database.execute(
                    "INSERT OR IGNORE INTO ids VALUES (?)", (identity,)
                ).rowcount
                == 0
            ):
                self.issue("duplicate_text_message_id")
        self._timing(record, kind)

    def _timing(self, record: Mapping[str, object], kind: str) -> None:
        if kind == "chat_ended":
            return
        timestamp = record.get("timestamp")
        if timestamp is None:
            self.missing_timestamps += 1
        elif type(timestamp) is not int or timestamp < 0:
            self.issue("invalid_timestamp")
        offset = record.get("time_in_seconds")
        if offset is None:
            return
        value = _offset(offset)
        if value is None:
            self.issue("invalid_replay_offset")
            return
        self.zero_without_timestamp += int(value == 0 and timestamp is None)
        self.minimum_offset = (
            min(self.minimum_offset, value)
            if self.minimum_offset is not None
            else value
        )
        self.maximum_offset = (
            max(self.maximum_offset, value)
            if self.maximum_offset is not None
            else value
        )
        self.offset_backsteps += int(
            self.previous_offset is not None and value < self.previous_offset
        )
        self.previous_offset = value

    def read(self, path: Path) -> None:
        for line in read_capture_lines(path):
            self.lines = line.line_number
            if line.issue is not None:
                self.issue(line.issue)
            elif line.record is not None:
                self.record(line.record)


def _accounting(
    summary: Mapping[str, object], inspection: _Inspection
) -> dict[str, object]:
    """Validate the fixed summary fields without exposing arbitrary source strings."""
    diagnostics = get_dict(summary, "provider_diagnostics")
    counters = {}
    for key in _COUNTERS:
        value = diagnostics.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError(_INVALID_SUMMARY)
        counters[key] = value
    count = summary.get("message_count")
    prior = summary.get("prior_message_count", 0)
    types = get_dict(summary, "message_type_counts")
    if (
        type(count) is not int
        or count < 0
        or type(prior) is not int
        or prior < 0
        or type(summary.get("success")) is not bool
        or not isinstance(summary.get("message_type_counts"), dict)
        or any(type(v) is not int or v < 0 for v in types.values())
    ):
        raise ValueError(_INVALID_SUMMARY)
    mismatches = sum(
        types.get(key, 0) != inspection.types.get(key, 0)
        for key in types.keys() | inspection.types.keys()
    )
    return {
        "counters": counters,
        "initial_request_profile": _choice(
            diagnostics.get("initial_request_profile"), _PROFILES
        ),
        "active_request_profile": _choice(
            diagnostics.get("active_request_profile"), _PROFILES
        ),
        "chat_view": _choice(diagnostics.get("chat_view"), _VIEWS),
        "summary_minus_records": count + prior - inspection.records,
        "type_counts_comparable": prior == 0,
        "message_type_count_mismatches": mismatches if prior == 0 else None,
        "run_failed": summary.get("success") is not True,
        "parity_failed": summary.get("parity_status") == "failed",
    }


def inspect_capture(
    path: Path | None, *, run_summary: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Inspect a single run; absent mobile timestamps remain an observation."""
    with (
        tempfile.TemporaryDirectory(prefix="youtube-inspection-") as directory,
        closing(sqlite3.connect(Path(directory) / "ids.sqlite")) as database,
    ):
        inspection = _Inspection(database)
        if path is not None:
            inspection.read(path)
        accounting = (
            _accounting(run_summary, inspection) if run_summary is not None else None
        )
        return {
            "jsonl_lines": inspection.lines,
            "records": inspection.records,
            "message_types": {k: v for k, v in inspection.types.items() if v},
            "issues": inspection.issues,
            "missing_source_timestamps": inspection.missing_timestamps,
            "zero_offset_without_timestamp": inspection.zero_without_timestamp,
            "minimum_offset_seconds": inspection.minimum_offset,
            "maximum_offset_seconds": inspection.maximum_offset,
            "offset_backsteps": inspection.offset_backsteps,
            "run_accounting": accounting,
            "status": "review"
            if inspection.issues
            or (
                accounting
                and (
                    accounting["summary_minus_records"]
                    or accounting["message_type_count_mismatches"]
                    or accounting["run_failed"]
                    or accounting["parity_failed"]
                    or get_dict(accounting, "counters")["parse_error"]
                )
            )
            else "ok",
        }

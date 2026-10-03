# SPDX-License-Identifier: MIT

"""Content-free, offline inspection of one Twitch live JSONL capture."""

from __future__ import annotations

import ast
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.sites.twitch.constants import MESSAGE_GROUPS
from chat_downloader.utils.capture_reader import open_capture_input, read_capture_lines
from chat_downloader.utils.json_types import get_dict, get_str

if TYPE_CHECKING:
    from collections.abc import Mapping

_KNOWN_TYPES = frozenset(t for group in MESSAGE_GROUPS.values() for t in group)
_LOCATION = re.compile(r"([0-9]+)-([0-9]+)")
_SUMMARY_PREFIX = re.compile(
    rb"(?:\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] )?"
    rb"\[DEBUG\] Run summary: "
)
_SUMMARY_LIMIT = 65536
_INVALID_SUMMARY = "invalid_run_summary"
_FRAME_KEYS = (
    "received_irc_frame_count",
    "benign_irc_control_frame_count",
    "parsed_irc_message_count",
)


def _emotes_valid(record: Mapping[str, object]) -> bool:
    """Validate every inclusive code-point range against its named emote."""
    emotes = record.get("emotes", [])
    if not isinstance(emotes, list):
        return False
    message = get_str(record, "message")
    for emote in emotes:
        if not isinstance(emote, dict) or not get_str(emote, "name"):
            return False
        locations = emote.get("locations")
        if not isinstance(locations, list) or not locations:
            return False
        for location in locations:
            match = _LOCATION.fullmatch(location) if isinstance(location, str) else None
            if match is None:
                return False
            # Reject oversized indices before int conversion (including its limit).
            if any(len(part) > 12 for part in match.groups()):
                return False
            start, end = map(int, match.groups())
            if not 0 <= start <= end < len(message):
                return False
            if message[start : end + 1] != emote["name"]:
                return False
    return True


def _account_frames(counters: Mapping[str, object]) -> dict[str, int]:
    """Validate the fixed counter schema and calculate unaccounted frames."""
    result = {}
    for key in _FRAME_KEYS:
        value = counters.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(_INVALID_SUMMARY)
        result[key] = value
    result["unaccounted_frames"] = result[_FRAME_KEYS[0]] - sum(
        result[key] for key in _FRAME_KEYS[1:]
    )
    return result


def _frame_accounting(path: Path) -> dict[str, int]:
    """Read exactly one bounded Python-literal run summary from a debug log."""
    result: dict[str, int] | None = None
    with open_capture_input(path) as source:
        for line in source:
            prefix = _SUMMARY_PREFIX.match(line)
            if prefix is None:
                continue
            if result is not None or len(line) > _SUMMARY_LIMIT:
                raise ValueError(_INVALID_SUMMARY)
            summary = ast.literal_eval(line[prefix.end() :].decode("utf-8"))
            if not isinstance(summary, dict):
                raise TypeError(_INVALID_SUMMARY)
            result = _account_frames(get_dict(summary, "provider_diagnostics"))
    if result is None:
        raise ValueError(_INVALID_SUMMARY)
    return result


class _Inspection:
    """Retain bounded diagnostics; exact ID membership lives in temporary SQLite."""

    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database
        database.execute("CREATE TABLE ids (id TEXT PRIMARY KEY) WITHOUT ROWID")
        self.lines = 0
        self.records = 0
        self.types = dict.fromkeys(sorted(_KNOWN_TYPES), 0)
        self.issues: dict[str, dict[str, int]] = {}
        self.previous_timestamp: int | None = None
        self.backsteps = 0
        self.max_backstep = 0
        self.first_backstep: int | None = None
        self.missing_timestamps = 0
        self.shapes = {"emotes": 0, "replies": 0, "badges": 0}

    def issue(self, kind: str) -> None:
        entry = self.issues.setdefault(kind, {"count": 0, "first_line": self.lines})
        entry["count"] += 1

    def record(self, record: Mapping[str, object]) -> None:
        self.records += 1
        message_type = get_str(record, "message_type")
        if message_type in self.types:
            self.types[message_type] += 1
        else:
            self.issue("unknown_message_type")
        self._identity(record, message_type)
        self._timestamp(record, message_type)
        if not _emotes_valid(record):
            self.issue("invalid_emote_locations")
        self.shapes["emotes"] += bool(record.get("emotes"))
        self.shapes["replies"] += bool(record.get("in_reply_to"))
        self.shapes["badges"] += bool(get_dict(record, "author").get("badges"))

    def _identity(self, record: Mapping[str, object], message_type: str) -> None:
        message_id = get_str(record, "message_id")
        if message_id:
            cursor = self.database.execute(
                "INSERT OR IGNORE INTO ids VALUES (?)", (message_id,)
            )
            if cursor.rowcount == 0:
                self.issue("duplicate_message_id")
        elif message_type == "text_message":
            self.issue("missing_text_message_id")
        if message_type == "text_message" and not get_str(
            get_dict(record, "author"), "id"
        ):
            self.issue("missing_text_author_id")

    def _timestamp(self, record: Mapping[str, object], message_type: str) -> None:
        timestamp = record.get("timestamp")
        if timestamp is None:
            self.missing_timestamps += 1
            if message_type == "text_message":
                self.issue("missing_text_timestamp")
            return
        if type(timestamp) is not int or timestamp < 0:
            self.issue("invalid_timestamp")
            return
        if self.previous_timestamp is not None and timestamp < self.previous_timestamp:
            self.backsteps += 1
            self.max_backstep = max(
                self.max_backstep, self.previous_timestamp - timestamp
            )
            if self.first_backstep is None:
                self.first_backstep = self.lines
        self.previous_timestamp = timestamp

    def read(self, path: Path) -> None:
        for line in read_capture_lines(path):
            self.lines = line.line_number
            if line.issue is not None:
                self.issue(line.issue)
            elif line.record is not None:
                self.record(line.record)

    def report(self) -> dict[str, object]:
        return {
            "jsonl_lines": self.lines,
            "records": self.records,
            "message_types": {key: value for key, value in self.types.items() if value},
            "issues": self.issues,
            "shape_counts": self.shapes,
            "missing_timestamps": self.missing_timestamps,
            "timestamp_backsteps": self.backsteps,
            "first_timestamp_backstep_line": self.first_backstep,
            "max_timestamp_backstep_microseconds": self.max_backstep,
        }


def inspect_capture(
    path: Path | None,
    debug_log: Path | None = None,
    *,
    diagnostics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Inspect one run without retaining message bodies or printing identifiers."""
    with (
        tempfile.TemporaryDirectory(prefix="twitch-inspection-") as directory,
        closing(sqlite3.connect(Path(directory) / "ids.sqlite")) as database,
    ):
        inspection = _Inspection(database)
        if path is not None:
            inspection.read(path)
        report = inspection.report()
    accounting = _frame_accounting(debug_log) if debug_log is not None else None
    if diagnostics is not None:
        accounting = _account_frames(diagnostics)
    report["frame_accounting"] = accounting
    report["status"] = (
        "review"
        if inspection.issues or (accounting and accounting["unaccounted_frames"] != 0)
        else "ok"
    )
    return report

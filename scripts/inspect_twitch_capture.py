# SPDX-License-Identifier: MIT

"""Content-free, offline inspection of one Twitch live JSONL capture."""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from chat_downloader.sites.twitch.constants import MESSAGE_GROUPS
from chat_downloader.utils.json_types import get_dict, get_str

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import BinaryIO

_KNOWN_TYPES = frozenset(t for group in MESSAGE_GROUPS.values() for t in group)
_LOCATION = re.compile(r"([0-9]+)-([0-9]+)")
_SUMMARY_PREFIX = b"[DEBUG] Run summary: "
_SUMMARY_LIMIT = 65536
_INVALID_SUMMARY = "invalid_run_summary"
_FRAME_KEYS = (
    "received_irc_frame_count",
    "benign_irc_control_frame_count",
    "parsed_irc_message_count",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - never echo input
        print('{"error": "invalid_arguments"}')
        raise SystemExit(2)


@contextmanager
def _open_input(path: Path) -> Iterator[BinaryIO]:
    """Reject special files without blocking on a FIFO waiting for a writer."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(descriptor)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous duplicate keys instead of silently keeping the last."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate_json(value: object) -> None:
    """Reject non-finite numbers and lone surrogates, even in unused fields."""
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json(key)
            _validate_json(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item)


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


def _frame_accounting(path: Path) -> dict[str, int]:
    """Read exactly one bounded Python-literal run summary from a debug log."""
    result: dict[str, int] | None = None
    with _open_input(path) as source:
        for line in source:
            if not line.startswith(_SUMMARY_PREFIX):
                continue
            if result is not None or len(line) > _SUMMARY_LIMIT:
                raise ValueError(_INVALID_SUMMARY)
            summary = ast.literal_eval(line[len(_SUMMARY_PREFIX) :].decode("utf-8"))
            if not isinstance(summary, dict):
                raise TypeError(_INVALID_SUMMARY)
            counters = get_dict(summary, "provider_diagnostics")
            result = {}
            for key in _FRAME_KEYS:
                value = counters.get(key)
                if type(value) is not int or value < 0:
                    raise ValueError(_INVALID_SUMMARY)
                result[key] = value
    if result is None:
        raise ValueError(_INVALID_SUMMARY)
    result["unaccounted_frames"] = result[_FRAME_KEYS[0]] - sum(
        result[key] for key in _FRAME_KEYS[1:]
    )
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
        with _open_input(path) as source:
            for self.lines, line in enumerate(source, 1):
                try:
                    record = json.loads(
                        line.decode("utf-8"), object_pairs_hook=_json_object
                    )
                    _validate_json(record)
                except (UnicodeError, ValueError, RecursionError):
                    self.issue("invalid_jsonl")
                    continue
                if not isinstance(record, dict):
                    self.issue("non_object_record")
                    continue
                self.record(record)

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


def inspect_capture(path: Path, debug_log: Path | None = None) -> dict[str, object]:
    """Inspect one run without retaining message bodies or printing identifiers."""
    with (
        tempfile.TemporaryDirectory(prefix="twitch-inspection-") as directory,
        closing(sqlite3.connect(Path(directory) / "ids.sqlite")) as database,
    ):
        inspection = _Inspection(database)
        inspection.read(path)
        report = inspection.report()
    accounting = _frame_accounting(debug_log) if debug_log is not None else None
    report["frame_accounting"] = accounting
    report["status"] = (
        "review"
        if inspection.issues or (accounting and accounting["unaccounted_frames"] != 0)
        else "ok"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """Print only aggregate findings; use exit 1 for review and 2 for input errors."""
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--debug-log", type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_capture(args.jsonl, args.debug_log)
    except (OSError, sqlite3.Error):
        print('{"error": "input_or_temporary_storage_io"}')
        return 2
    except (ValueError, TypeError, SyntaxError, RecursionError):
        print('{"error": "invalid_run_summary"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "review" else 0


if __name__ == "__main__":
    raise SystemExit(main())

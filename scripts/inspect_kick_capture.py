# SPDX-License-Identifier: MIT

"""Content-free, offline inspection of one Kick live JSONL capture."""

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

from chat_downloader.sites.kick.constants import MESSAGE_GROUPS
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
    "websocket_frame_count",
    "control_frame_count",
    "parsed_event_count",
    "unsupported_event_count",
    "unknown_message_type_count",
    "malformed_event_count",
    "invalid_websocket_frame_count",
    "websocket_reconnect_count",
    "pusher_error_count",
    "pusher_key_recovery_count",
    "preloaded_emitted_count",
    "live_emitted_count",
    "reconnect_backfill_emitted_count",
)
_ANOMALIES = (
    "unsupported_event_count",
    "unknown_message_type_count",
    "malformed_event_count",
    "invalid_websocket_frame_count",
    "pusher_error_count",
)
_GAPS = (
    "unaccounted_frames",
    "emitted_minus_records",
    "summary_minus_records",
    "malformed_type_count_gap",
    "message_type_count_mismatches",
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


def _emote_texts(emote: Mapping[str, object]) -> set[str]:
    """Return permitted renderings for an ID, including mixed unnamed markers."""
    name = emote.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        return set()
    expected = {f":emote_{emote['id']}:"}
    if name:
        expected.add(f":{name}:")
    return expected


def _emotes_valid(record: Mapping[str, object]) -> bool:
    """Validate every inclusive code-point range against its named emote."""
    emotes = record.get("emotes", [])
    if not isinstance(emotes, list):
        return False
    message = get_str(record, "message")
    for emote in emotes:
        if not isinstance(emote, dict) or not get_str(emote, "id"):
            return False
        expected = _emote_texts(emote)
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
            # One ID may mix named and unnamed markers; metadata stores one name.
            if message[start : end + 1] not in expected:
                return False
    return True


def _decode_summary(line: bytes) -> dict[str, object]:
    """Reject ambiguous dictionary keys before evaluating a literal summary."""
    tree = ast.parse(line.decode("utf-8"), mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [ast.literal_eval(key) for key in node.keys if key is not None]
            if len(keys) != len(set(keys)):
                raise ValueError(_INVALID_SUMMARY)
    summary: object = ast.literal_eval(tree)
    if not isinstance(summary, dict):
        raise TypeError(_INVALID_SUMMARY)
    return summary


def _run_summary(path: Path) -> dict[str, object]:
    """Read exactly one bounded literal summary, without retaining log content."""
    result: dict[str, object] | None = None
    with _open_input(path) as source:
        while line := source.readline(_SUMMARY_LIMIT + 1):
            is_summary = line.startswith(_SUMMARY_PREFIX)
            if is_summary:
                if result is not None or len(line) > _SUMMARY_LIMIT:
                    raise ValueError(_INVALID_SUMMARY)
                result = _decode_summary(line[len(_SUMMARY_PREFIX) :])
            # Consume long unrelated log lines without treating their tail as a header.
            while not line.endswith(b"\n") and len(line) == _SUMMARY_LIMIT + 1:
                line = source.readline(_SUMMARY_LIMIT + 1)
    if result is None:
        raise ValueError(_INVALID_SUMMARY)
    return result


def _counter(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(_INVALID_SUMMARY)
    return value


def _type_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(_INVALID_SUMMARY)
    return {key: _counter(value, key) for key in value}


def _frame_accounting(path: Path, inspection: _Inspection) -> dict[str, int]:
    """Reconcile decoded frames and emitted records; expose only fixed keys."""
    summary = _run_summary(path)
    counters = get_dict(summary, "provider_diagnostics")
    result = {key: _counter(counters, key) for key in _FRAME_KEYS}
    if type(summary.get("success")) is not bool:
        raise ValueError(_INVALID_SUMMARY)
    result["run_failed"] = int(not summary["success"])
    result["unaccounted_frames"] = result["websocket_frame_count"] - sum(
        result[key]
        for key in (
            "control_frame_count",
            "parsed_event_count",
            "unsupported_event_count",
            "malformed_event_count",
            "pusher_error_count",
        )
    )
    result["emitted_minus_records"] = (
        sum(
            result[key]
            for key in (
                "preloaded_emitted_count",
                "live_emitted_count",
                "reconnect_backfill_emitted_count",
            )
        )
        - inspection.records
    )
    result["summary_minus_records"] = (
        _counter(summary, "message_count") - inspection.records
    )
    malformed = _type_counts(counters.get("malformed_event_type_counts"))
    for message_type in sorted(_KNOWN_TYPES):
        if count := malformed.get(message_type, 0):
            result[f"malformed_{message_type}"] = count
    result["malformed_type_count_gap"] = result["malformed_event_count"] - sum(
        malformed.values()
    )
    reported = _type_counts(summary.get("message_type_counts"))
    actual = {key: count for key, count in inspection.types.items() if count}
    result["message_type_count_mismatches"] = sum(
        reported.get(key, 0) != actual.get(key, 0)
        for key in reported.keys() | actual.keys()
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
        reply = record.get("in_reply_to")
        if reply is not None and (
            not isinstance(reply, dict) or not _emotes_valid(reply)
        ):
            self.issue("invalid_reply_emote_locations")
        if message_type == "text_message" and not get_str(record, "message"):
            self.issue("missing_text_content")
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
        else:
            self.issue("missing_message_id")
        if message_type == "text_message" and not get_str(
            get_dict(record, "author"), "id"
        ):
            self.issue("missing_text_author_id")

    def _timestamp(self, record: Mapping[str, object], message_type: str) -> None:
        received = record.get("received_timestamp")
        if received is not None and (type(received) is not int or received < 0):
            self.issue("invalid_received_timestamp")
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
        tempfile.TemporaryDirectory(prefix="kick-inspection-") as directory,
        closing(sqlite3.connect(Path(directory) / "ids.sqlite")) as database,
    ):
        inspection = _Inspection(database)
        inspection.read(path)
        report = inspection.report()
    accounting = (
        _frame_accounting(debug_log, inspection) if debug_log is not None else None
    )
    report["frame_accounting"] = accounting
    report["status"] = (
        "review"
        if inspection.issues
        or (
            accounting
            and any(accounting[key] != 0 for key in (*_ANOMALIES, *_GAPS, "run_failed"))
        )
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

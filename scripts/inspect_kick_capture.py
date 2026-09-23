# SPDX-License-Identifier: MIT

"""Content-free, offline inspection of Kick live or replay JSONL captures."""

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
from datetime import datetime
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


def _frame_accounting(
    summary: dict[str, object], inspection: _Inspection
) -> dict[str, int]:
    """Reconcile decoded frames and emitted records; expose only fixed keys."""
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


def _replay_accounting(  # noqa: C901 - validate and reconcile one replay ledger
    summaries: list[dict[str, object]], inspection: _Inspection
) -> dict[str, object]:
    """Reconcile replay runs without interpreting file parity as completeness."""
    totals = dict.fromkeys(
        (
            "pages",
            "raw_records",
            "emitted_records",
            "skipped_records",
            "filtered_records",
            "duplicate_records",
            "checkpoint_overlap_suppressed",
            "message_count",
        ),
        0,
    )
    reported: dict[str, int] = {}
    reasons: list[str] = []
    parities: list[str] = []
    statuses: dict[str, int] = {}
    bounds: list[tuple[int, int]] = []
    needs_review = bool(inspection.backsteps or inspection.missing_timestamps)
    raw_gap = 0
    for index, summary in enumerate(summaries):
        state = get_dict(summary, "provider_diagnostics")
        if (
            state.get("protocol") != "reverse"
            or type(summary.get("success")) is not bool
        ):
            raise ValueError(_INVALID_SUMMARY)
        reason, parity = summary.get("termination_reason"), summary.get("parity_status")
        if reason not in {
            "completed",
            "empty_window",
            "message_limit",
            "timeout",
            "inactivity_timeout",
            "interrupted",
            "error",
        } or parity not in {"passed", "failed", "not_run", "not_requested"}:
            raise ValueError(_INVALID_SUMMARY)
        reasons.append(str(reason))
        parities.append(str(parity))
        needs_review |= (
            not summary["success"]
            or (
                index == len(summaries) - 1
                and reason not in {"completed", "empty_window"}
            )
            or reason == "error"
            or parity in {"failed", "not_run"}
        )
        if "history_complete" in state and type(state["history_complete"]) is not bool:
            raise ValueError(_INVALID_SUMMARY)
        needs_review |= (
            state.get("history_complete") is not True and reason != "empty_window"
        )
        for key in totals:
            source = summary if key == "message_count" else state
            required = key in {
                "pages",
                "raw_records",
                "emitted_records",
                "message_count",
            }
            totals[key] += _counter(
                source if required or key in source else {key: 0}, key
            )
        for key, count in _type_counts(summary.get("message_type_counts")).items():
            reported[key] = reported.get(key, 0) + count
        for key in ("malformed_timestamp", "malformed_object", "parse_error"):
            needs_review |= _counter(state, key) > 0 if key in state else False
        selected = (
            _counter(state, "selected_records")
            if "selected_records" in state
            else _counter(state, "emitted_records")
        )
        if reason == "completed" or "selected_records" in state:
            page_gap = (
                _counter(state, "raw_records")
                - selected
                - sum(
                    _counter(state, key) if key in state else 0
                    for key in (
                        "skipped_records",
                        "filtered_records",
                        "duplicate_records",
                    )
                )
            )
            raw_gap += page_gap
            needs_review |= page_gap != 0
        needs_review |= sum(
            _type_counts(summary.get("message_type_counts")).values()
        ) != _counter(summary, "message_count")
        prior_loss = state.get("prior_record_loss", False)
        if type(prior_loss) is not bool:
            raise ValueError(_INVALID_SUMMARY)
        needs_review |= prior_loss
        needs_review |= _counter(state, "emitted_records") - (
            _counter(state, "checkpoint_overlap_suppressed")
            if "checkpoint_overlap_suppressed" in state
            else 0
        ) != _counter(summary, "message_count")
        start, end = state.get("requested_start"), state.get("requested_end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError(_INVALID_SUMMARY)
        dates = [datetime.fromisoformat(value) for value in (start, end)]
        if any(value.tzinfo is None for value in dates):
            raise ValueError(_INVALID_SUMMARY)
        bounds.append(
            (
                int(dates[0].timestamp() * 1_000_000),
                int(dates[1].timestamp() * 1_000_000),
            )
        )
        transport = get_dict(state, "transport")
        for key, count in _type_counts(transport.get("http_status_counts", {})).items():
            if key != "transport_error" and (
                len(key) != 3 or not key.isascii() or not key.isdigit()
            ):
                raise ValueError(_INVALID_SUMMARY)
            statuses[key] = statuses.get(key, 0) + count
    observed = (inspection.minimum_timestamp, inspection.maximum_timestamp)
    outside = any(
        value is not None
        and not min(a for a, _ in bounds) <= value <= max(b for _, b in bounds)
        for value in observed
    )
    actual = {key: count for key, count in inspection.types.items() if count}
    type_gap = sum(
        reported.get(key, 0) != actual.get(key, 0)
        for key in reported.keys() | actual.keys()
    )
    count_gap = totals["message_count"] - inspection.records
    emitted_gap = (
        totals["emitted_records"]
        - totals["checkpoint_overlap_suppressed"]
        - totals["message_count"]
    )
    return {
        **totals,
        "termination_reasons": reasons,
        "parity_statuses": parities,
        "http_status_counts": statuses,
        "requested_bounds_microseconds": bounds,
        "observed_bounds_microseconds": observed,
        "raw_accounting_gap": raw_gap,
        "summary_minus_records": count_gap,
        "emitted_minus_written": emitted_gap,
        "message_type_count_mismatches": type_gap,
        "outside_requested_bounds": outside,
        "needs_review": bool(
            needs_review or raw_gap or count_gap or emitted_gap or type_gap or outside
        ),
    }


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
        self.minimum_timestamp: int | None = None
        self.maximum_timestamp: int | None = None
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
        self.minimum_timestamp = (
            min(self.minimum_timestamp, timestamp)
            if self.minimum_timestamp is not None
            else timestamp
        )
        self.maximum_timestamp = (
            max(self.maximum_timestamp, timestamp)
            if self.maximum_timestamp is not None
            else timestamp
        )
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


def inspect_capture(
    path: Path, debug_log: Path | list[Path] | None = None
) -> dict[str, object]:
    """Inspect live or appended replay captures without retaining content."""
    with (
        tempfile.TemporaryDirectory(prefix="kick-inspection-") as directory,
        closing(sqlite3.connect(Path(directory) / "ids.sqlite")) as database,
    ):
        inspection = _Inspection(database)
        inspection.read(path)
        report = inspection.report()
    paths = (
        debug_log if isinstance(debug_log, list) else [debug_log] if debug_log else []
    )
    summaries = [_run_summary(path) for path in paths]
    replay = bool(
        summaries
        and get_dict(summaries[0], "provider_diagnostics").get("protocol") == "reverse"
    )
    if len(summaries) > 1 and not replay:
        raise ValueError(_INVALID_SUMMARY)
    accounting = (
        _frame_accounting(summaries[0], inspection)
        if summaries and not replay
        else None
    )
    replay_accounting = _replay_accounting(summaries, inspection) if replay else None
    report["replay_accounting"] = replay_accounting
    report["frame_accounting"] = accounting
    report["status"] = (
        "review"
        if inspection.issues
        or (replay_accounting and replay_accounting["needs_review"])
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
    parser.add_argument(
        "--debug-log",
        type=Path,
        action="append",
        help="One log per capture run, in append order; repeat for resumed replay",
    )
    args = parser.parse_args(argv)
    try:
        report = inspect_capture(args.jsonl, args.debug_log)
    except (OSError, sqlite3.Error):
        print('{"error": "input_or_temporary_storage_io"}')
        return 2
    except (ValueError, TypeError, SyntaxError, RecursionError, OverflowError):
        print('{"error": "invalid_run_summary"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "review" else 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.inspect_kick_capture import inspect_capture, main


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


def _text(message_id="one", timestamp=1000):
    return {
        "message_type": "text_message",
        "message_id": message_id,
        "author": {"id": "author", "badges": [{"name": "subscriber"}]},
        "timestamp": timestamp,
        "message": "日本語 :Kappa: :Kappa:",
        "emotes": [{"id": "1", "name": "Kappa", "locations": ["4-10", "12-18"]}],
    }


def _clean_rows():
    return [
        {"message_type": "poll_deleted", "message_id": "poll"},
        _text(),
        _text("two", 982),
    ]


@pytest.fixture
def capture(tmp_path):
    return _write(tmp_path / "chat.jsonl", _clean_rows())


def _log(path, received=5, benign=2, parsed=3, **changes):
    summary = {
        "success": True,
        "message_count": 3,
        "message_type_counts": {"poll_deleted": 1, "text_message": 2},
    }
    state = dict.fromkeys(
        (
            "unsupported_event_count",
            "unknown_message_type_count",
            "malformed_event_count",
            "invalid_websocket_frame_count",
            "websocket_reconnect_count",
            "pusher_error_count",
            "pusher_key_recovery_count",
            "reconnect_backfill_emitted_count",
        ),
        0,
    )
    state.update(
        websocket_frame_count=received,
        control_frame_count=benign,
        parsed_event_count=parsed,
        malformed_event_type_counts={},
        preloaded_emitted_count=1,
        live_emitted_count=2,
    )
    summary["provider_diagnostics"] = state
    for key, value in changes.items():
        (summary if key in summary else state)[key] = value
    return _summary(path, summary)


def _summary(path, summary):
    path.write_text("[DEBUG] Run summary: " + repr(summary) + "\n", encoding="utf-8")
    return path


def _replay_log(path, count=1, termination_reason="completed", **changes):
    state = {
        "protocol": "reverse",
        "pages": 1,
        "raw_records": count + 1,
        "emitted_records": count,
        "skipped_records": 1,
        "history_complete": True,
        "requested_start": "1970-01-01T00:00:00Z",
        "requested_end": "1970-01-01T00:00:10Z",
        "transport": {"http_status_counts": {"200": 1, "404": 1}},
    }
    return _summary(
        path,
        {
            "success": True,
            "termination_reason": termination_reason,
            "parity_status": "passed",
            "message_count": count,
            "message_type_counts": {"text_message": count},
            "provider_diagnostics": state | changes,
        },
    )


def _assert_bad_summary(capture, log, capsys):
    assert main([str(capture), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_clean_inspection_counts_shapes_and_informational_backsteps(tmp_path):
    rows = _clean_rows()
    rows[-1]["in_reply_to"] = {"message_id": "parent"}
    report = inspect_capture(_write(tmp_path / "chat", rows), _log(tmp_path / "log"))
    expected = {
        "status": "ok",
        "records": 3,
        "message_types": {"poll_deleted": 1, "text_message": 2},
        "issues": {},
        "shape_counts": {"emotes": 2, "replies": 1, "badges": 2},
        "missing_timestamps": 1,
        "timestamp_backsteps": 1,
        "max_timestamp_backstep_microseconds": 18,
        "first_timestamp_backstep_line": 3,
    }
    assert {key: report[key] for key in expected} == expected
    assert report["frame_accounting"]["unaccounted_frames"] == 0


def test_scans_past_bad_records_without_echoing_content(tmp_path, capsys):
    sentinel = "PRIVATE_SENTINEL"
    rows = [
        _text(sentinel),
        _text(sentinel),
        {"message_type": sentinel},
        _text("three", True),
    ]
    rows[-1]["emotes"] = [{"name": "Kappa", "locations": ["999-1000"]}]
    path = _write(tmp_path / "chat", rows)
    with path.open("ab") as stream:
        stream.write(
            b'[]\n{bad PRIVATE_SENTINEL\n\xff\n\n{"message_type":"text_message"}\n'
        )
    assert main([str(path)]) == 1
    output = capsys.readouterr().out
    assert sentinel not in output
    report = json.loads(output)
    assert (report["jsonl_lines"], report["records"]) == (9, 5)
    for issue, count, line in [
        ("duplicate_message_id", 1, 2),
        ("missing_message_id", 2, 3),
        ("invalid_jsonl", 3, 6),
        ("invalid_emote_locations", 1, 4),
    ]:
        assert report["issues"][issue] == {"count": count, "first_line": line}
    report = inspect_capture(
        _write(
            path,
            [
                {
                    "message_type": "stream_host",
                    "message_id": "host",
                    "received_timestamp": True,
                }
            ],
        )
    )
    assert report["issues"] == {
        "invalid_received_timestamp": {"count": 1, "first_line": 1},
    }


@pytest.mark.parametrize(
    ("fields", "emotes"),
    [
        *[({}, value) for value in [None, {}, [None], [{}]]],
        *[
            ({}, [{"id": "1", "name": "Kappa", "locations": locations}])
            for locations in [[], "4-8", [None], ["-1-3"], ["8-4"], ["1" * 5000 + "-8"]]
        ],
        ({}, [{"id": "1", "name": "Wrong", "locations": ["4-8"]}]),
        *[
            ({"message": "::"}, [{"id": "1", "name": name, "locations": ["0-1"]}])
            for name in [None, "", 42, {}, []]
        ],
    ],
)
def test_invalid_emote_shapes_are_findings_not_crashes(tmp_path, fields, emotes):
    report = inspect_capture(
        _write(
            tmp_path / "chat",
            [
                _text() | fields | {"emotes": emotes},
            ],
        )
    )
    assert report["issues"]["invalid_emote_locations"]["count"] == 1


@pytest.mark.parametrize("received", [4, 6])
def test_positive_and_negative_frame_gaps_need_review(tmp_path, received):
    report = inspect_capture(
        _write(tmp_path / "chat", []), _log(tmp_path / "log", received)
    )
    assert report["status"] == "review"
    assert report["frame_accounting"]["unaccounted_frames"] == received - 5


@pytest.mark.parametrize(
    "summary",
    [
        "duplicate",
        "prefix",
        "appended",
        "",
        "[DEBUG] Run summary: []\n",
        "[DEBUG] Run summary: {'provider_diagnostics': {}}\n",
        "[DEBUG] Run summary: __import__('os').system('PRIVATE_SENTINEL')\n",
        "[DEBUG] Run summary: " + "x" * 65536,
    ],
)
def test_bad_summary_fails_without_echoing_input(tmp_path, capsys, capture, summary):
    log = _log(tmp_path / "log")
    text = log.read_text()
    if summary == "duplicate":
        summary = text.replace(
            "'malformed_event_count': 0",
            "'malformed_event_count': 1, 'malformed_event_count': 0",
        )
    elif summary in {"prefix", "appended"}:
        summary = "x" * 65537 + text if summary == "prefix" else text * 2
    log.write_text(summary, encoding="utf-8")
    _assert_bad_summary(capture, log, capsys)


@pytest.mark.parametrize("kind", ["missing", "directory", "usage"])
def test_io_and_usage_errors_are_content_free(tmp_path, capsys, kind):
    if kind == "usage":
        with pytest.raises(SystemExit) as error:
            main(["--PRIVATE_SENTINEL"])
        assert error.value.code == 2
        assert "PRIVATE_SENTINEL" not in capsys.readouterr().out
    else:
        path = tmp_path if kind == "directory" else tmp_path / "PRIVATE_SENTINEL"
        assert main([str(path)]) == 2
        assert json.loads(capsys.readouterr().out) == {
            "error": "input_or_temporary_storage_io",
        }


@pytest.mark.parametrize(
    "fifo",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"), reason="POSIX special-file contract"
            ),
        ),
    ],
)
def test_script_entry_point_empty_or_nonblocking_fifo(tmp_path, fifo):
    path = tmp_path / "input"
    if fifo:
        os.mkfifo(path)
    else:
        _write(path, [])
    script = Path(__file__).resolve().parents[1] / "scripts" / "inspect_kick_capture.py"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and project script
        [sys.executable, str(script), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5 if fifo else 10,
    )
    assert result.returncode == (2 if fifo else 0)
    report = json.loads(result.stdout)
    if fifo:
        assert report == {"error": "input_or_temporary_storage_io"}
        assert result.stderr == ""
    else:
        assert (report["status"], report["records"], report["frame_accounting"]) == (
            "ok",
            0,
            None,
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"message_type":"poll_deleted","message_type":"text_message"}',
        '{"message_type":"poll_deleted","unused":NaN}',
        '{"message_type":"poll_deleted","unused":[Infinity]}',
        '{"message_type":"poll_deleted","unused":1e9999}',
        r'{"message_type":"poll_deleted","unused":"\ud800"}',
        r'{"message_type":"poll_deleted","unused":{"\ud800":0}}',
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
def test_ambiguous_or_unencodable_json_is_bounded(tmp_path, raw):
    path = tmp_path / "chat"
    path.write_text(raw + '\n{"message_type":"poll_deleted"}\n', encoding="utf-8")
    report = inspect_capture(path)
    assert report["records"] == 1
    assert report["issues"]["invalid_jsonl"] == {"count": 1, "first_line": 1}


def test_exact_duplicate_detection_includes_distant_ids_and_sql_characters(tmp_path):
    rows = [_text(str(i)) for i in range(12000)]
    rows.extend([_text("0"), _text("'); DROP TABLE ids; --"), _text("12001")])
    report = inspect_capture(_write(tmp_path / "chat", rows))
    assert report["records"] == 12003
    assert report["issues"] == {
        "duplicate_message_id": {"count": 1, "first_line": 12001},
    }


@pytest.mark.parametrize(
    ("replay", "changes", "status"),
    [
        *[
            (False, c, "review")
            for c in [
                {"success": False},
                {"message_count": 4},
                {"message_type_counts": {"text_message": 3}},
                {"live_emitted_count": 3},
                *[
                    {key: 1}
                    for key in (
                        "unsupported_event_count",
                        "unknown_message_type_count",
                        "invalid_websocket_frame_count",
                        "pusher_error_count",
                    )
                ],
                {"malformed_event_type_counts": {"stream_host": 1}},
            ]
        ],
        *[
            (False, c, "error")
            for c in [
                {"success": 1},
                {"message_count": True},
                {"message_type_counts": []},
                {"message_type_counts": {"text_message": -1}},
                {"malformed_event_type_counts": {1: 1}},
                *[{"received": counter} for counter in (True, -1, "5", None)],
            ]
        ],
        (
            False,
            {
                "websocket_reconnect_count": 1,
                "live_emitted_count": 1,
                "reconnect_backfill_emitted_count": 1,
            },
            "ok",
        ),
        *[
            (True, c, "review")
            for c in [
                {"parse_error": 1},
                {"malformed_timestamp": 1},
                {"history_complete": False},
                {"raw_records": 99},
                {"emitted_records": 0},
                {"skipped_records": 0},
                {"requested_end": "1970-01-01T00:00:00Z"},
                {"prior_record_loss": True},
                *[
                    {"termination_reason": reason}
                    for reason in (
                        "timeout",
                        "inactivity_timeout",
                        "message_limit",
                        "interrupted",
                        "error",
                    )
                ],
            ]
        ],
        *[
            (True, c, "error")
            for c in [
                {"pages": True},
                {"history_complete": "yes"},
                {"requested_start": "PRIVATE_SENTINEL"},
                {"requested_end": None},
                {"requested_start": "1970-01-01T00:00:00"},
                {"transport": {"http_status_counts": {"PRIVATE_SENTINEL": 1}}},
            ]
        ],
    ],
)
def test_summary_findings(tmp_path, capsys, replay, changes, status):
    capture = _write(tmp_path / "chat", [_text()] if replay else _clean_rows())
    log = (_replay_log if replay else _log)(tmp_path / "log", **changes)
    if status == "error":
        _assert_bad_summary(capture, log, capsys)
    else:
        assert inspect_capture(capture, log)["status"] == status


@pytest.mark.parametrize("private", [False, True, "unknown"])
def test_accounted_parser_drop_and_private_names(tmp_path, capsys, capture, private):
    log = _log(
        tmp_path / "log",
        received=7 if private else 6,
        malformed_event_count=2 if private else 1,
        malformed_event_type_counts={"stream_host": 1}
        | ({"PRIVATE_SENTINEL": 1} if private else {}),
    )
    if private == "unknown":
        log = _log(log, message_type_counts={"PRIVATE_SENTINEL": 3})
        log.write_text("PRIVATE_SENTINEL" * 10000 + "\n" + log.read_text())
    assert main([str(capture), "--debug-log", str(log)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    accounting = json.loads(output)["frame_accounting"]
    if private != "unknown":
        assert accounting["malformed_stream_host"] == 1
    if not private:
        assert (
            accounting["unaccounted_frames"],
            accounting["emitted_minus_records"],
            accounting["malformed_event_count"],
        ) == (0, 0, 1)


def test_named_unnamed_and_reply_emotes(tmp_path):
    row = _text() | {
        "message": ":emote_1: :Kappa:",
        "emotes": [{"id": "1", "name": "Kappa", "locations": ["0-8", "10-16"]}],
        "in_reply_to": {
            "message": ":emote_2:",
            "emotes": [{"id": "2", "name": None, "locations": ["0-8"]}],
        },
    }
    path = _write(tmp_path / "chat", [row])
    assert inspect_capture(path)["status"] == "ok"
    row["in_reply_to"]["emotes"][0]["locations"] = ["0-1"]
    report = inspect_capture(_write(path, [row]))
    assert "invalid_reply_emote_locations" in report["issues"]


@pytest.mark.parametrize("kind", ["legacy", "resumed", "opposite", "live"])
def test_multi_run_accounting(tmp_path, kind):
    rows = [_text()]
    if kind == "legacy":
        logs = _replay_log(tmp_path / "log")
    elif kind == "live":
        with pytest.raises(ValueError, match="invalid_run_summary"):
            inspect_capture(
                _write(tmp_path / "chat", []),
                [_log(tmp_path / "one"), _log(tmp_path / "two")],
            )
        return
    else:
        rows.append(_text("two", 2000))
        first = {"raw_records": 3}
        second = {"raw_records": 1}
        if kind == "resumed":
            first.update(selected_records=2, termination_reason="message_limit")
            second = {
                "emitted_records": 2,
                "checkpoint_overlap_suppressed": 1,
                "raw_records": 3,
            }
        logs = [
            _replay_log(tmp_path / "one", **first),
            _replay_log(tmp_path / "two", **second),
        ]
    report = inspect_capture(_write(tmp_path / "chat", rows), logs)
    assert report["status"] == ("review" if kind == "opposite" else "ok")
    accounting = report["replay_accounting"]
    if kind == "resumed":
        assert accounting["message_count"] == 2
    else:
        assert accounting["raw_accounting_gap"] == 0
    if kind == "legacy":
        assert report["frame_accounting"] is None
        assert accounting["http_status_counts"] == {"200": 1, "404": 1}

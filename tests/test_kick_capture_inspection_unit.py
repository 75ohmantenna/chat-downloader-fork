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


def _log(path, received=5, benign=2, parsed=3):
    summary = {
        "success": True,
        "message_count": 3,
        "message_type_counts": {"poll_deleted": 1, "text_message": 2},
        "provider_diagnostics": {
            "websocket_frame_count": received,
            "control_frame_count": benign,
            "parsed_event_count": parsed,
            "unsupported_event_count": 0,
            "unknown_message_type_count": 0,
            "malformed_event_count": 0,
            "malformed_event_type_counts": {},
            "invalid_websocket_frame_count": 0,
            "websocket_reconnect_count": 0,
            "pusher_error_count": 0,
            "pusher_key_recovery_count": 0,
            "preloaded_emitted_count": 1,
            "live_emitted_count": 2,
            "reconnect_backfill_emitted_count": 0,
        },
    }
    path.write_text("[DEBUG] Run summary: " + repr(summary) + "\n", encoding="utf-8")
    return path


def test_clean_inspection_counts_shapes_and_keeps_timestamp_backsteps_informational(
    tmp_path,
):
    rows = [
        {"message_type": "poll_deleted", "message_id": "poll"},
        _text(),
        _text("two", 982),
    ]
    rows[-1]["in_reply_to"] = {"message_id": "parent"}
    report = inspect_capture(
        _write(tmp_path / "chat.jsonl", rows), _log(tmp_path / "debug.log")
    )
    assert report["status"] == "ok"
    assert report["records"] == 3
    assert report["message_types"] == {"poll_deleted": 1, "text_message": 2}
    assert report["issues"] == {}
    assert report["shape_counts"] == {"emotes": 2, "replies": 1, "badges": 2}
    assert report["missing_timestamps"] == 1
    assert report["timestamp_backsteps"] == 1
    assert report["max_timestamp_backstep_microseconds"] == 18
    assert report["first_timestamp_backstep_line"] == 3
    assert report["frame_accounting"]["unaccounted_frames"] == 0


def test_inspection_scans_past_bad_records_and_never_echoes_content(tmp_path, capsys):
    path = tmp_path / "chat.jsonl"
    sentinel = "PRIVATE_SENTINEL"
    rows = [
        _text(sentinel),
        _text(sentinel),
        {"message_type": sentinel},
        _text("three", True),
    ]
    rows[-1]["emotes"] = [{"name": "Kappa", "locations": ["999-1000"]}]
    _write(path, rows)
    with path.open("ab") as stream:
        stream.write(b"[]\n{bad PRIVATE_SENTINEL\n\xff\n\n")
        stream.write(b'{"message_type":"text_message"}\n')
    assert main([str(path)]) == 1
    output = capsys.readouterr().out
    assert sentinel not in output
    report = json.loads(output)
    assert report["jsonl_lines"] == 9
    assert report["records"] == 5
    assert report["issues"]["duplicate_message_id"] == {"count": 1, "first_line": 2}
    assert report["issues"]["missing_message_id"] == {"count": 2, "first_line": 3}
    assert report["issues"]["invalid_jsonl"] == {"count": 3, "first_line": 6}
    assert report["issues"]["invalid_emote_locations"] == {"count": 1, "first_line": 4}


@pytest.mark.parametrize(
    "emotes",
    [
        None,
        {},
        [None],
        [{}],
        [{"id": "1", "name": "Kappa", "locations": []}],
        [{"id": "1", "name": "Kappa", "locations": "4-8"}],
        [{"id": "1", "name": "Kappa", "locations": [None]}],
        [{"id": "1", "name": "Kappa", "locations": ["-1-3"]}],
        [{"id": "1", "name": "Kappa", "locations": ["8-4"]}],
        [{"id": "1", "name": "Wrong", "locations": ["4-8"]}],
        [{"id": "1", "name": "Kappa", "locations": ["1" * 5000 + "-8"]}],
    ],
)
def test_invalid_emote_shapes_are_findings_not_crashes(tmp_path, emotes):
    row = _text()
    row["emotes"] = emotes
    report = inspect_capture(_write(tmp_path / "chat.jsonl", [row]))
    assert report["issues"]["invalid_emote_locations"]["count"] == 1


@pytest.mark.parametrize("received", [4, 6])
def test_positive_and_negative_frame_gaps_need_review(tmp_path, received):
    report = inspect_capture(
        _write(tmp_path / "chat.jsonl", []), _log(tmp_path / "debug.log", received)
    )
    assert report["status"] == "review"
    assert report["frame_accounting"]["unaccounted_frames"] == received - 5


@pytest.mark.parametrize(
    "summary",
    [
        "",
        "[DEBUG] Run summary: []\n",
        "[DEBUG] Run summary: {'provider_diagnostics': {}}\n",
        "[DEBUG] Run summary: __import__('os').system('PRIVATE_SENTINEL')\n",
        "[DEBUG] Run summary: " + "x" * 65536,
    ],
)
def test_bad_summary_fails_without_echoing_input(tmp_path, capsys, summary):
    path = _write(tmp_path / "chat.jsonl", [])
    log = tmp_path / "debug.log"
    log.write_text(summary, encoding="utf-8")
    assert main([str(path), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_appended_run_summaries_are_rejected(tmp_path, capsys):
    path = _write(tmp_path / "chat.jsonl", [])
    log = _log(tmp_path / "debug.log")
    log.write_text(log.read_text() * 2)
    assert main([str(path), "--debug-log", str(log)]) == 2
    assert "invalid_run_summary" in capsys.readouterr().out


def test_io_and_usage_errors_are_content_free(tmp_path, capsys):
    assert main([str(tmp_path / "PRIVATE_SENTINEL")]) == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["--PRIVATE_SENTINEL"])
    assert error.value.code == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out


def test_script_entry_point_handles_empty_capture(tmp_path):
    path = _write(tmp_path / "chat.jsonl", [])
    result = subprocess.run(  # noqa: S603 - fixed interpreter and project script
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "inspect_kick_capture.py"
            ),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["records"] == 0
    assert report["frame_accounting"] is None


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
def test_ambiguous_or_unencodable_json_is_a_bounded_finding(tmp_path, raw):
    path = tmp_path / "chat.jsonl"
    path.write_text(raw + '\n{"message_type":"poll_deleted"}\n', encoding="utf-8")
    report = inspect_capture(path)
    assert report["records"] == 1
    assert report["issues"]["invalid_jsonl"] == {"count": 1, "first_line": 1}


@pytest.mark.parametrize("counter", [True, -1, "5", None])
def test_invalid_frame_counter_values_are_input_errors(tmp_path, capsys, counter):
    path = _write(tmp_path / "chat.jsonl", [])
    log = _log(tmp_path / "debug.log", received=counter)
    assert main([str(path), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_exact_duplicate_detection_includes_distant_ids_and_sql_characters(tmp_path):
    rows = [_text(str(i)) for i in range(12000)]
    rows.extend([_text("0"), _text("'); DROP TABLE ids; --"), _text("12001")])
    report = inspect_capture(_write(tmp_path / "chat.jsonl", rows))
    assert report["records"] == 12003
    assert report["issues"] == {
        "duplicate_message_id": {"count": 1, "first_line": 12001}
    }


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file contract")
def test_fifo_input_fails_promptly_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "input.fifo"
    os.mkfifo(path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "inspect_kick_capture.py"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and project script
        [sys.executable, str(script), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "input_or_temporary_storage_io"}
    assert result.stderr == ""


def test_directory_input_is_an_io_error(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "input_or_temporary_storage_io"
    }


def _alter_log(path, **changes):
    import ast

    prefix = "[DEBUG] Run summary: "
    summary = ast.literal_eval(path.read_text()[len(prefix) :])
    for key, value in changes.items():
        if key in {"success", "message_count", "message_type_counts"}:
            summary[key] = value
        else:
            summary["provider_diagnostics"][key] = value
    path.write_text(prefix + repr(summary) + "\n")
    return path


def _clean_rows():
    return [
        {"message_type": "poll_deleted", "message_id": "poll"},
        _text(),
        _text("two", 982),
    ]


def test_accounted_parser_drop_still_requires_review(tmp_path):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(
        _log(tmp_path / "debug.log", received=6),
        malformed_event_count=1,
        malformed_event_type_counts={"stream_host": 1},
    )
    report = inspect_capture(capture, log)
    assert report["status"] == "review"
    assert report["frame_accounting"]["unaccounted_frames"] == 0
    assert report["frame_accounting"]["emitted_minus_records"] == 0
    assert report["frame_accounting"]["malformed_event_count"] == 1


def test_reconnect_backfill_is_informational_with_consistent_totals(tmp_path):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(
        _log(tmp_path / "debug.log"),
        websocket_reconnect_count=1,
        live_emitted_count=1,
        reconnect_backfill_emitted_count=1,
    )
    assert inspect_capture(capture, log)["status"] == "ok"


@pytest.mark.parametrize(
    "changes",
    [
        {"success": False},
        {"message_count": 4},
        {"message_type_counts": {"text_message": 3}},
        {"live_emitted_count": 3},
        {"unsupported_event_count": 1},
        {"unknown_message_type_count": 1},
        {"invalid_websocket_frame_count": 1},
        {"pusher_error_count": 1},
        {"malformed_event_type_counts": {"stream_host": 1}},
    ],
)
def test_summary_anomalies_require_review(tmp_path, changes):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(_log(tmp_path / "debug.log"), **changes)
    assert inspect_capture(capture, log)["status"] == "review"


@pytest.mark.parametrize(
    "changes",
    [
        {"success": 1},
        {"message_count": True},
        {"message_type_counts": []},
        {"message_type_counts": {"text_message": -1}},
        {"malformed_event_type_counts": {1: 1}},
    ],
)
def test_invalid_summary_shapes_fail_closed(tmp_path, capsys, changes):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(_log(tmp_path / "debug.log"), **changes)
    assert main([str(capture), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_summary_never_echoes_unknown_types_or_other_log_content(tmp_path, capsys):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(
        _log(tmp_path / "debug.log"), message_type_counts={"PRIVATE_SENTINEL": 3}
    )
    log.write_text("PRIVATE_SENTINEL" * 10000 + "\n" + log.read_text())
    assert main([str(capture), "--debug-log", str(log)]) == 1
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().out


def test_named_unnamed_and_reply_emotes(tmp_path):
    row = _text()
    row["message"] = ":emote_1: :Kappa:"
    row["emotes"] = [{"id": "1", "name": "Kappa", "locations": ["0-8", "10-16"]}]
    row["in_reply_to"] = {
        "message": ":emote_2:",
        "emotes": [{"id": "2", "name": None, "locations": ["0-8"]}],
    }
    path = _write(tmp_path / "chat.jsonl", [row])
    assert inspect_capture(path)["status"] == "ok"
    row["in_reply_to"]["emotes"][0]["locations"] = ["0-1"]
    assert (
        "invalid_reply_emote_locations"
        in inspect_capture(_write(path, [row]))["issues"]
    )


def test_receive_timestamp_validation(tmp_path):
    rows = [
        {
            "message_type": "stream_host",
            "message_id": "host",
            "received_timestamp": True,
        }
    ]
    report = inspect_capture(_write(tmp_path / "chat.jsonl", rows))
    assert report["issues"] == {
        "invalid_received_timestamp": {"count": 1, "first_line": 1}
    }


@pytest.mark.parametrize("name", [None, "", 42, {}, []])
def test_empty_or_invalid_emote_name_cannot_validate_bare_colons(tmp_path, name):
    row = _text()
    row.update(message="::", emotes=[{"id": "1", "name": name, "locations": ["0-1"]}])
    report = inspect_capture(_write(tmp_path / "chat.jsonl", [row]))
    assert "invalid_emote_locations" in report["issues"]


def test_duplicate_summary_keys_are_rejected(tmp_path, capsys):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _log(tmp_path / "debug.log")
    log.write_text(
        log.read_text().replace(
            "'malformed_event_count': 0",
            "'malformed_event_count': 1, 'malformed_event_count': 0",
        )
    )
    assert main([str(capture), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_log_line_tail_cannot_forge_summary(tmp_path, capsys):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _log(tmp_path / "debug.log")
    log.write_bytes(b"x" * 65537 + log.read_bytes())
    assert main([str(capture), "--debug-log", str(log)]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_run_summary"}


def test_malformed_type_names_are_bounded_and_content_free(tmp_path, capsys):
    capture = _write(tmp_path / "chat.jsonl", _clean_rows())
    log = _alter_log(
        _log(tmp_path / "debug.log", received=7),
        malformed_event_count=2,
        malformed_event_type_counts={"stream_host": 1, "PRIVATE_SENTINEL": 1},
    )
    assert main([str(capture), "--debug-log", str(log)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    assert json.loads(output)["frame_accounting"]["malformed_stream_host"] == 1

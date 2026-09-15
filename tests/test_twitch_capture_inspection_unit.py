# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.inspect_twitch_capture import inspect_capture, main


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
        "message": "日本語 Kappa Kappa",
        "emotes": [{"name": "Kappa", "locations": ["4-8", "10-14"]}],
    }


def _log(path, received=5, benign=2, parsed=3):
    summary = {
        "provider_diagnostics": dict(
            zip(
                [
                    "received_irc_frame_count",
                    "benign_irc_control_frame_count",
                    "parsed_irc_message_count",
                ],
                [received, benign, parsed],
                strict=True,
            )
        )
    }
    path.write_text("[DEBUG] Run summary: " + repr(summary) + "\n", encoding="utf-8")
    return path


def test_clean_inspection_counts_shapes_and_keeps_timestamp_backsteps_informational(
    tmp_path,
):
    rows = [{"message_type": "room_state"}, _text(), _text("two", 982)]
    rows[-1]["in_reply_to"] = {"message_id": "parent"}
    report = inspect_capture(
        _write(tmp_path / "chat.jsonl", rows), _log(tmp_path / "debug.log")
    )
    assert report["status"] == "ok"
    assert report["records"] == 3
    assert report["message_types"] == {"room_state": 1, "text_message": 2}
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
    assert report["issues"] == {
        "duplicate_message_id": {"count": 1, "first_line": 2},
        "unknown_message_type": {"count": 1, "first_line": 3},
        "invalid_timestamp": {"count": 1, "first_line": 4},
        "invalid_emote_locations": {"count": 1, "first_line": 4},
        "non_object_record": {"count": 1, "first_line": 5},
        "invalid_jsonl": {"count": 3, "first_line": 6},
        "missing_text_message_id": {"count": 1, "first_line": 9},
        "missing_text_author_id": {"count": 1, "first_line": 9},
        "missing_text_timestamp": {"count": 1, "first_line": 9},
    }


@pytest.mark.parametrize(
    "emotes",
    [
        None,
        {},
        [None],
        [{}],
        [{"name": "Kappa", "locations": []}],
        [{"name": "Kappa", "locations": "4-8"}],
        [{"name": "Kappa", "locations": [None]}],
        [{"name": "Kappa", "locations": ["-1-3"]}],
        [{"name": "Kappa", "locations": ["8-4"]}],
        [{"name": "Wrong", "locations": ["4-8"]}],
        [{"name": "Kappa", "locations": ["1" * 5000 + "-8"]}],
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
                / "inspect_twitch_capture.py"
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
        '{"message_type":"room_state","message_type":"text_message"}',
        '{"message_type":"room_state","unused":NaN}',
        '{"message_type":"room_state","unused":[Infinity]}',
        '{"message_type":"room_state","unused":1e9999}',
        r'{"message_type":"room_state","unused":"\ud800"}',
        r'{"message_type":"room_state","unused":{"\ud800":0}}',
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
def test_ambiguous_or_unencodable_json_is_a_bounded_finding(tmp_path, raw):
    path = tmp_path / "chat.jsonl"
    path.write_text(raw + '\n{"message_type":"room_state"}\n', encoding="utf-8")
    report = inspect_capture(path)
    assert report["records"] == 1
    assert report["issues"] == {"invalid_jsonl": {"count": 1, "first_line": 1}}


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
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "inspect_twitch_capture.py"
    )
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

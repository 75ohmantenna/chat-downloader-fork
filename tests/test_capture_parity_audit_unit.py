# SPDX-License-Identifier: MIT

"""Offline JSONL/TXT capture parity auditor contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import (
    AuditStats,
    audit_capture,
)
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_capture_parity.py"


def _run_audit(
    jsonl_path: Path,
    txt_path: Path,
    *,
    format_name: str = "default",
    format_file: Path | None = None,
    max_seen_message_ids: int | None = None,
    dedup_reset_before_lines: tuple[int, ...] = (),
    timeout_seconds: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        str(jsonl_path),
        str(txt_path),
        "--format",
        format_name,
    ]
    if format_file is not None:
        command.extend(("--format-file", str(format_file)))
    if max_seen_message_ids is not None:
        command.extend(("--max-seen-message-ids", str(max_seen_message_ids)))
    for line_number in dedup_reset_before_lines:
        command.extend(("--dedup-reset-before-jsonl-line", str(line_number)))
    return subprocess.run(  # noqa: S603 - fixed interpreter and project script
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _write_jsonl(path: Path, records: list[object]) -> None:
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )


def _write_message_format(path: Path) -> None:
    path.write_text(
        json.dumps({"audit": {"template": "{message_type}:{message}"}}),
        encoding="utf-8",
    )


def _write_plain_format(path: Path) -> None:
    path.write_text(
        json.dumps({"audit": {"template": "{message}"}}),
        encoding="utf-8",
    )


def _run_raw(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and project script
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _audit_direct(
    jsonl_path: Path,
    txt_path: Path,
    *,
    format_name: str = "default",
    format_file: Path | None = None,
    max_seen_message_ids: int = 10_000,
    dedup_reset_before_lines: tuple[int, ...] = (),
) -> AuditStats:
    return audit_capture(
        jsonl_path,
        txt_path,
        formatter=ItemFormatter(str(format_file) if format_file else None),
        format_name=format_name,
        max_seen_message_ids=max_seen_message_ids,
        dedup_reset_before_lines=dedup_reset_before_lines,
    )


def test_auditor_matches_real_writer_composition_and_semantic_dedup(
    tmp_path: Path,
) -> None:
    """Exercise writer dispatch, formatting, deduplication, and subprocess audit."""
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    format_file = tmp_path / "formats.json"
    _write_message_format(format_file)
    messages = [
        {
            "message_id": "paid-1",
            "message_type": "paid_message",
            "message": "private-paid\nline\x01",
        },
        {
            "message_id": "paid-1",
            "message_type": "ticker_paid_message_item",
            "message": "private-paid\nline\x01",
        },
        {
            "message_id": "text-1",
            "message_type": "text_message",
            "message": "private-generic\u2028line",
        },
        {
            "message_id": "text-1",
            "message_type": "text_message",
            "message": "private-generic\u2028line",
        },
    ]
    formatter = ItemFormatter(str(format_file))
    chat = Chat(chat=iter(messages))
    chat.set_formatter(lambda item: formatter.format(item, format_name="audit"))
    chat.attach_writer(ContinuousWriter(str(jsonl_path), lazy_initialise=True))
    chat.attach_writer(ContinuousWriter(str(txt_path), lazy_initialise=True))

    list(chat)
    chat.close()

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
    )
    direct = _audit_direct(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not direct.failed
    assert direct.suppressed_duplicates == 1
    assert "PASS" in result.stdout
    assert "jsonl_records=4" in result.stdout
    assert "expected_txt_lines=3" in result.stdout
    assert "txt_lines=3" in result.stdout
    assert "suppressed_duplicates=1" in result.stdout
    assert "trailing_newline=yes" in result.stdout
    assert "private" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("raw_jsonl", "reason"),
    [
        (b"\n", "jsonl_blank_line"),
        (b"{not-json}\n", "jsonl_invalid_json"),
        (b"[1, 2]\n", "jsonl_non_object"),
        (b"\xff\n", "jsonl_invalid_utf8"),
    ],
)
def test_auditor_rejects_invalid_jsonl_records_without_echoing_them(
    tmp_path: Path,
    raw_jsonl: bytes,
    reason: str,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(raw_jsonl)
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.jsonl_errors == 1
    assert f"first_issue={reason}" in result.stdout
    assert "jsonl_errors=1" in result.stdout
    assert "not-json" not in result.stdout + result.stderr


def test_auditor_scans_every_jsonl_line_after_an_invalid_record(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"\xff\n{}\n\n")
    txt_path.write_bytes(b"\n")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert "jsonl_lines=3" in result.stdout
    assert "jsonl_records=1" in result.stdout
    assert "jsonl_errors=2" in result.stdout
    assert "first_issue_jsonl_line=1" in result.stdout
    assert direct.comparison_skipped == 3
    assert "comparison_complete=no" in result.stdout
    assert "comparison_skipped=3" in result.stdout


def _content_error_summary(
    *,
    jsonl_records: int,
    jsonl_errors: int,
    dedup_errors: int,
    first_issue: str,
) -> str:
    return (
        "FAIL jsonl_lines=1 "
        f"jsonl_records={jsonl_records} expected_txt_lines=0 txt_lines=0 "
        "suppressed_duplicates=0 "
        f"jsonl_errors={jsonl_errors} dedup_errors={dedup_errors} "
        "render_errors=0 txt_utf8_errors=0 text_mismatches=0 "
        "newline_errors=0 count_mismatch=no jsonl_trailing_newline=yes "
        "txt_trailing_newline=empty jsonl_newline_style=lf "
        "txt_newline_style=none newline_style_mismatch=no "
        "comparison_complete=no comparison_skipped=1 "
        "dedup_resets_applied=0 dedup_reset_errors=0 jsonl_missing=no "
        f"txt_missing=no first_issue={first_issue} "
        "first_issue_jsonl_line=1 first_issue_txt_line=- "
        "first_mismatch_jsonl_line=- first_mismatch_txt_line=-\n"
    )


def test_auditor_contains_deep_json_recursion_as_content_error(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    nesting = max(10_000, sys.getrecursionlimit() * 10)
    raw_json = b"[" * nesting + b"{}" + b"]" * nesting
    with pytest.raises(RecursionError):
        json.loads(raw_json.decode())
    jsonl_path.write_bytes(raw_json + b"\n")
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert result.stdout == _content_error_summary(
        jsonl_records=0,
        jsonl_errors=1,
        dedup_errors=0,
        first_issue="jsonl_invalid_json",
    )
    assert result.stderr == ""
    assert direct.first_issue == "jsonl_invalid_json"


def test_auditor_contains_unhashable_dedup_fields_as_content_error(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    _write_jsonl(
        jsonl_path,
        [{"message_type": ["private-type"], "message": "private-message"}],
    )
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert result.stdout == _content_error_summary(
        jsonl_records=1,
        jsonl_errors=0,
        dedup_errors=1,
        first_issue="dedup_error",
    )
    assert result.stderr == ""
    assert "private" not in result.stdout
    assert direct.dedup_errors == 1


def test_auditor_reports_comparisons_skipped_after_render_error(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    _write_jsonl(
        jsonl_path,
        [
            {
                "message_type": "text_message",
                "author": {"badges": [1]},
                "message": "private-error",
            },
            {"message_type": "text_message", "message": "private-after"},
        ],
    )
    txt_path.write_bytes(b": private-after\n")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert "render_errors=1" in result.stdout
    assert "comparison_complete=no" in result.stdout
    assert "comparison_skipped=2" in result.stdout
    assert "first_issue=render_error" in result.stdout
    assert result.stderr == ""
    assert "private" not in result.stdout
    assert direct.render_errors == 1


def test_auditor_reports_exact_mismatch_indices_and_count_without_content(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    _write_jsonl(
        jsonl_path,
        [
            {"message_type": "text_message", "message": "private-one"},
            {"message_type": "text_message", "message": "private-two"},
        ],
    )
    txt_path.write_text(": altered\n", encoding="utf-8")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.text_mismatches == 2
    assert "text_mismatches=2" in result.stdout
    assert "count_mismatch=yes" in result.stdout
    assert "first_mismatch_jsonl_line=1" in result.stdout
    assert "first_mismatch_txt_line=1" in result.stdout
    assert "private" not in result.stdout + result.stderr
    assert "altered" not in result.stdout + result.stderr


def test_auditor_rejects_invalid_txt_utf8_and_missing_trailing_newline(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    _write_jsonl(
        jsonl_path,
        [{"message_type": "text_message", "message": "private"}],
    )
    txt_path.write_bytes(b"\xff")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.txt_utf8_errors == 1
    assert direct.txt_trailing_newline is False
    assert "txt_utf8_errors=1" in result.stdout
    assert "trailing_newline=no" in result.stdout
    assert "first_issue=txt_invalid_utf8" in result.stdout
    assert "private" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("jsonl_newline", "txt_newline", "expected_code"),
    [
        (b"\n", b"\n", 0),
        (b"\r\n", b"\r\n", 0),
        (b"\n", b"\r\n", 1),
        (b"\r\n", b"\n", 1),
    ],
)
def test_auditor_validates_capture_origin_newlines_and_sanitized_content(
    tmp_path: Path,
    jsonl_newline: bytes,
    txt_newline: bytes,
    expected_code: int,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    format_file = tmp_path / "formats.json"
    _write_plain_format(format_file)
    private_message = "cr\rnel\x85ls\u2028ps\u2029end"
    record = {"message_type": "text_message", "message": private_message}
    jsonl_path.write_bytes(json.dumps(record).encode() + jsonl_newline)
    sanitized = b"cr\\rnel\\u0085ls\\u2028ps\\u2029end"
    txt_path.write_bytes(sanitized + txt_newline)

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
    )
    direct = _audit_direct(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
    )

    assert result.returncode == expected_code
    assert direct.failed is (expected_code == 1)
    assert result.stderr == ""
    if expected_code == 0:
        assert "PASS" in result.stdout
        assert "newline_errors=0" in result.stdout
        assert "newline_style_mismatch=no" in result.stdout
    else:
        assert "FAIL" in result.stdout
        assert "first_issue=capture_newline_style_mismatch" in result.stdout
        assert "newline_style_mismatch=yes" in result.stdout
    assert private_message not in result.stdout


def test_auditor_requires_jsonl_final_newline(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    record = {"message_type": "text_message", "message": "private"}
    jsonl_path.write_bytes(json.dumps(record).encode())
    txt_path.write_bytes(b": private\n")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.jsonl_trailing_newline is False
    assert "jsonl_trailing_newline=no" in result.stdout
    assert "first_issue=jsonl_missing_trailing_newline" in result.stdout
    assert "first_mismatch_jsonl_line=1" in result.stdout
    assert result.stderr == ""
    assert "private" not in result.stdout


def test_auditor_rejects_mixed_jsonl_newline_styles(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    first = json.dumps({"message_type": "text_message", "message": "one"})
    second = json.dumps({"message_type": "text_message", "message": "two"})
    jsonl_path.write_bytes(first.encode() + b"\n" + second.encode() + b"\r\n")
    txt_path.write_bytes(b": one\n: two\n")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.jsonl_mixed_newlines == 1
    assert "jsonl_newline_style=mixed" in result.stdout
    assert "first_issue=jsonl_mixed_newlines" in result.stdout
    assert "first_mismatch_jsonl_line=2" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("create_empty", [False, True])
def test_auditor_accepts_empty_and_lazy_missing_artifacts(
    tmp_path: Path,
    create_empty: bool,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    if create_empty:
        jsonl_path.write_bytes(b"")
        txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not direct.failed
    assert "PASS" in result.stdout
    assert "jsonl_records=0" in result.stdout
    assert "txt_lines=0" in result.stdout
    assert "jsonl_trailing_newline=empty" in result.stdout
    assert "txt_trailing_newline=empty" in result.stdout
    missing = "no" if create_empty else "yes"
    assert f"jsonl_missing={missing}" in result.stdout
    assert f"txt_missing={missing}" in result.stdout


def test_auditor_treats_one_lazy_missing_empty_artifact_as_empty(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not direct.failed
    assert "jsonl_missing=no" in result.stdout
    assert "txt_missing=yes" in result.stdout


def test_auditor_rejects_an_extra_physical_txt_line(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    _write_jsonl(
        jsonl_path,
        [{"message_type": "text_message", "message": "private"}],
    )
    txt_path.write_text(": private\nextra\n", encoding="utf-8")

    result = _run_audit(jsonl_path, txt_path)
    direct = _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 1
    assert direct.count_mismatch
    assert "count_mismatch=yes" in result.stdout
    assert "first_mismatch_jsonl_line=-" in result.stdout
    assert "first_mismatch_txt_line=2" in result.stdout
    assert "private" not in result.stdout + result.stderr
    assert "extra" not in result.stdout + result.stderr


def test_auditor_matches_production_dedup_cache_eviction(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    format_file = tmp_path / "formats.json"
    _write_message_format(format_file)
    records = [
        {"message_id": "one", "message_type": "paid_message", "message": "a"},
        {"message_id": "two", "message_type": "paid_message", "message": "b"},
        {
            "message_id": "one",
            "message_type": "ticker_paid_message_item",
            "message": "c",
        },
    ]
    _write_jsonl(jsonl_path, records)
    txt_path.write_text(
        "paid_message:a\npaid_message:b\nticker_paid_message_item:c\n",
        encoding="utf-8",
    )

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        max_seen_message_ids=1,
    )
    direct = _audit_direct(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        max_seen_message_ids=1,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not direct.failed
    assert "suppressed_duplicates=0" in result.stdout
    assert "expected_txt_lines=3" in result.stdout


def test_auditor_normalizes_zero_dedup_limit_to_production_default(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    format_file = tmp_path / "formats.json"
    _write_message_format(format_file)
    records = [
        {"message_id": "same", "message_type": "paid_message", "message": "a"},
        {
            "message_id": "same",
            "message_type": "ticker_paid_message_item",
            "message": "b",
        },
    ]
    _write_jsonl(jsonl_path, records)
    txt_path.write_text("paid_message:a\n", encoding="utf-8")

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        max_seen_message_ids=0,
    )
    direct = _audit_direct(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        max_seen_message_ids=0,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert direct.suppressed_duplicates == 1
    assert "suppressed_duplicates=1" in result.stdout


def test_auditor_resets_dedup_for_appended_logical_runs(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    format_file = tmp_path / "formats.json"
    _write_message_format(format_file)
    records = [
        {"message_id": "same", "message_type": "paid_message", "message": "a"},
        {
            "message_id": "same",
            "message_type": "ticker_paid_message_item",
            "message": "b",
        },
        {"message_id": "same", "message_type": "paid_message", "message": "c"},
    ]
    _write_jsonl(jsonl_path, records)
    txt_path.write_text(
        "paid_message:a\nticker_paid_message_item:b\npaid_message:c\n",
        encoding="utf-8",
    )

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        dedup_reset_before_lines=(2, 3),
    )
    direct = _audit_direct(
        jsonl_path,
        txt_path,
        format_name="audit",
        format_file=format_file,
        dedup_reset_before_lines=(2, 3),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert direct.dedup_resets_applied == 2
    assert "dedup_resets_applied=2" in result.stdout
    assert "suppressed_duplicates=0" in result.stdout


@pytest.mark.parametrize("configuration", ["unknown", "missing", "invalid"])
def test_auditor_returns_usage_code_for_format_configuration_errors(
    tmp_path: Path,
    configuration: str,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"")
    txt_path.write_bytes(b"")
    format_name = "unknown"
    format_file = None
    if configuration == "missing":
        format_name = "audit"
        format_file = tmp_path / "missing.json"
    elif configuration == "invalid":
        format_name = "audit"
        format_file = tmp_path / "invalid.json"
        format_file.write_text("not-json-private", encoding="utf-8")

    result = _run_audit(
        jsonl_path,
        txt_path,
        format_name=format_name,
        format_file=format_file,
    )

    assert result.returncode == 2
    assert result.stdout.startswith("ERROR kind=")
    assert "private" not in result.stdout + result.stderr


def test_auditor_returns_io_code_without_echoing_input_paths(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "private-jsonl-directory"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.mkdir()
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    with pytest.raises(OSError):
        _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=input_io\n"
    assert "private-jsonl-directory" not in result.stdout + result.stderr


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named FIFOs are unavailable")
def test_auditor_rejects_named_fifo_without_blocking(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "private-capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    os.mkfifo(jsonl_path)
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path, timeout_seconds=2.0)
    with pytest.raises(OSError, match="not a regular file"):
        _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=input_io\n"
    assert result.stderr == ""
    assert "private-capture" not in result.stdout


@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_auditor_rejects_inputs_with_the_same_opened_identity(
    tmp_path: Path,
    link_kind: str,
) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"{}\n")
    if link_kind == "hardlink":
        os.link(jsonl_path, txt_path)
    else:
        txt_path.symlink_to(jsonl_path)

    result = _run_audit(jsonl_path, txt_path)
    with pytest.raises(OSError):
        _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=input_identity\n"
    assert result.stderr == ""


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX owner permissions require a non-root test process",
)
def test_auditor_treats_permission_denial_as_io_not_lazy_missing(
    tmp_path: Path,
) -> None:
    jsonl_path = tmp_path / "private-capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"{}\n")
    jsonl_path.chmod(0)
    txt_path.write_bytes(b"")
    try:
        result = _run_audit(jsonl_path, txt_path)
        with pytest.raises(PermissionError):
            _audit_direct(jsonl_path, txt_path)
    finally:
        jsonl_path.chmod(0o600)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=input_io\n"
    assert result.stderr == ""
    assert "private-capture" not in result.stdout


def test_auditor_rejects_a_dangling_input_symlink(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.symlink_to(tmp_path / "missing-target")
    txt_path.write_bytes(b"")

    result = _run_audit(jsonl_path, txt_path)
    with pytest.raises(OSError):
        _audit_direct(jsonl_path, txt_path)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=input_io\n"
    assert result.stderr == ""


def test_auditor_rejects_out_of_range_dedup_reset(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    jsonl_path.write_bytes(b"")
    txt_path.write_bytes(b"")

    direct = _audit_direct(
        jsonl_path,
        txt_path,
        dedup_reset_before_lines=(1,),
    )

    assert direct.failed
    assert not direct.comparison_complete
    assert direct.dedup_reset_errors == 1
    assert direct.first_issue == "dedup_reset_out_of_range"


@pytest.mark.parametrize(
    "arguments",
    [
        ("private-jsonl", "private-txt"),
        (
            "private-jsonl",
            "private-txt",
            "--format",
            "default",
            "--max-seen-message-ids",
            "-1",
        ),
        (
            "private-jsonl",
            "private-txt",
            "--format",
            "default",
            "--private-option",
            "private-value",
        ),
        (
            "private-jsonl",
            "private-txt",
            "--format",
            "default",
            "--dedup-reset-before-jsonl-line",
            "3",
            "--dedup-reset-before-jsonl-line",
            "2",
        ),
    ],
)
def test_auditor_argument_errors_are_fixed_and_content_free(
    arguments: tuple[str, ...],
) -> None:
    result = _run_raw(*arguments)

    assert result.returncode == 2
    assert result.stdout == "ERROR kind=invalid_arguments\n"
    assert result.stderr == ""


def test_auditor_streams_a_large_capture(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "capture.jsonl"
    txt_path = tmp_path / "capture.txt"
    record_count = 2_500
    _write_jsonl(
        jsonl_path,
        [
            {"message_type": "text_message", "message": f"message-{index}"}
            for index in range(record_count)
        ],
    )
    txt_path.write_text(
        "".join(f": message-{index}\n" for index in range(record_count)),
        encoding="utf-8",
    )

    result = _run_audit(jsonl_path, txt_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"jsonl_records={record_count}" in result.stdout
    assert f"txt_lines={record_count}" in result.stdout

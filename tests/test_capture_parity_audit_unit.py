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
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_capture_parity.py"


def _run_raw(*arguments, timeout=5.0):
    return subprocess.run(  # noqa: S603 - fixed interpreter and project script
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class Capture:
    def __init__(self, root):
        self.jsonl = root / "capture.jsonl"
        self.txt = root / "capture.txt"
        self.format_file = None
        self.format_name = "default"

    def write(self, records, text=b""):
        raw = (
            records
            if isinstance(records, bytes)
            else b"".join(json.dumps(record).encode() + b"\n" for record in records)
        )
        self.jsonl.write_bytes(raw)
        self.txt.write_bytes(text if isinstance(text, bytes) else text.encode())

    def format(self, template):
        self.format_file = self.jsonl.parent / "formats.json"
        self.format_file.write_text(
            json.dumps({"audit": {"template": template}}), encoding="utf-8"
        )
        self.format_name = "audit"

    def direct(self, **options):
        return audit_capture(
            self.jsonl,
            self.txt,
            formatter=ItemFormatter(
                str(self.format_file) if self.format_file else None
            ),
            format_name=self.format_name,
            **options,
        )

    def run(self, *, timeout=5.0, **options):
        args = [self.jsonl, self.txt, "--format", self.format_name]
        if self.format_file is not None:
            args.extend(("--format-file", self.format_file))
        if "max_seen_message_ids" in options:
            args.extend(("--max-seen-message-ids", options["max_seen_message_ids"]))
        for line in options.get("dedup_reset_before_lines", ()):
            args.extend(("--dedup-reset-before-jsonl-line", line))
        return _run_raw(*args, timeout=timeout)

    def check(self, code=0, *, counters=None, absent=("private",), **options):
        result = self.run(**options)
        direct = self.direct(**options)
        assert result.returncode == code, result.stdout + result.stderr
        assert direct.failed is bool(code)
        assert result.stderr == ""
        assert result.stdout.startswith("FAIL " if code else "PASS ")
        assert result.stdout.endswith("\n")
        for token in absent:
            assert token not in result.stdout + result.stderr
        fields = dict(word.split("=", 1) for word in result.stdout.split()[1:])
        for name, expected in (counters or {}).items():
            assert fields[name] == str(expected), (name, fields)
        return direct


@pytest.fixture
def capture(tmp_path):
    return Capture(tmp_path)


def _message(message="private", **fields):
    return {"message_type": "text_message", "message": message, **fields}


def test_auditor_matches_real_writer_composition_and_semantic_dedup(capture):
    capture.format("{message_type}:{message}")
    messages = [
        _message("private-paid\nline\x01", message_id="paid-1", message_type=kind)
        for kind in ("paid_message", "ticker_paid_message_item")
    ] + [_message("private-generic\u2028line", message_id="text-1")] * 2
    formatter = ItemFormatter(str(capture.format_file))
    chat = Chat(chat=iter(messages))
    chat.set_formatter(lambda item: formatter.format(item, format_name="audit"))
    for path in (capture.jsonl, capture.txt):
        chat.attach_writer(ContinuousWriter(str(path), lazy_initialise=True))
    list(chat)
    chat.close()
    stats = capture.check(
        counters={
            "jsonl_records": 4,
            "expected_txt_lines": 3,
            "txt_lines": 3,
            "suppressed_duplicates": 1,
            "txt_trailing_newline": "yes",
        }
    )
    assert stats.suppressed_duplicates == 1


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"\n", "jsonl_blank_line"),
        (b"{not-json}\n", "jsonl_invalid_json"),
        (b"[1, 2]\n", "jsonl_non_object"),
        (b"\xff\n", "jsonl_invalid_utf8"),
    ],
)
def test_invalid_jsonl_without_content_echo(capture, raw, reason):
    capture.write(raw)
    stats = capture.check(
        1,
        counters={"first_issue": reason, "jsonl_errors": 1},
        absent=("private", "not-json"),
    )
    assert stats.jsonl_errors == 1


@pytest.mark.parametrize(
    ("records", "text", "counters", "direct"),
    [
        (
            b"\xff\n{}\n\n",
            b"\n",
            {
                "jsonl_lines": 3,
                "jsonl_records": 1,
                "jsonl_errors": 2,
                "first_issue_jsonl_line": 1,
                "comparison_complete": "no",
                "comparison_skipped": 3,
            },
            {"comparison_skipped": 3},
        ),
        (
            [
                _message("private-error", author={"badges": [1]}),
                _message("private-after"),
            ],
            b": private-after\n",
            {
                "render_errors": 1,
                "comparison_complete": "no",
                "comparison_skipped": 2,
                "first_issue": "render_error",
            },
            {"render_errors": 1},
        ),
        (
            [_message("private-one"), _message("private-two")],
            b": altered\n",
            {
                "text_mismatches": 2,
                "count_mismatch": "yes",
                "first_mismatch_jsonl_line": 1,
                "first_mismatch_txt_line": 1,
            },
            {"text_mismatches": 2},
        ),
        (
            [_message()],
            b"\xff",
            {
                "txt_utf8_errors": 1,
                "txt_trailing_newline": "no",
                "first_issue": "txt_invalid_utf8",
            },
            {"txt_utf8_errors": 1, "txt_trailing_newline": False},
        ),
        (
            json.dumps(_message()).encode(),
            b": private\n",
            {
                "jsonl_trailing_newline": "no",
                "first_issue": "jsonl_missing_trailing_newline",
                "first_mismatch_jsonl_line": 1,
            },
            {"jsonl_trailing_newline": False},
        ),
        (
            json.dumps(_message("one")).encode()
            + b"\n"
            + json.dumps(_message("two")).encode()
            + b"\r\n",
            b": one\n: two\n",
            {
                "jsonl_newline_style": "mixed",
                "first_issue": "jsonl_mixed_newlines",
                "first_mismatch_jsonl_line": 2,
            },
            {"jsonl_mixed_newlines": 1},
        ),
        (
            [_message()],
            b": private\nextra\n",
            {
                "count_mismatch": "yes",
                "first_mismatch_jsonl_line": "-",
                "first_mismatch_txt_line": 2,
            },
            {"count_mismatch": True},
        ),
    ],
)
def test_content_errors_and_scan_continuation(capture, records, text, counters, direct):
    capture.write(records, text)
    stats = capture.check(1, counters=counters, absent=("private", "altered", "extra"))
    for name, expected in direct.items():
        assert getattr(stats, name) == expected


@pytest.mark.parametrize("deep_json", [True, False])
def test_decoder_depth_and_unhashable_dedup_are_content_errors(capture, deep_json):
    nesting = max(10_000, sys.getrecursionlimit() * 10)
    capture.write(
        b"[" * nesting + b"{}" + b"]" * nesting + b"\n"
        if deep_json
        else [{"message_type": ["private-type"], "message": "private-message"}]
    )
    stats = capture.direct()
    assert stats.first_issue in (
        {"jsonl_invalid_json", "jsonl_non_object"} if deep_json else {"dedup_error"}
    )
    capture.check(
        1,
        counters={
            "jsonl_lines": 1,
            "jsonl_records": int(not deep_json),
            "expected_txt_lines": 0,
            "txt_lines": 0,
            "suppressed_duplicates": 0,
            "jsonl_errors": int(deep_json),
            "dedup_errors": int(not deep_json),
            "render_errors": 0,
            "txt_utf8_errors": 0,
            "text_mismatches": 0,
            "newline_errors": 0,
            "count_mismatch": "no",
            "jsonl_trailing_newline": "yes",
            "txt_trailing_newline": "empty",
            "jsonl_newline_style": "lf",
            "txt_newline_style": "none",
            "newline_style_mismatch": "no",
            "comparison_complete": "no",
            "comparison_skipped": 1,
            "dedup_resets_applied": 0,
            "dedup_reset_errors": 0,
            "jsonl_missing": "no",
            "txt_missing": "no",
            "first_issue": stats.first_issue,
            "first_issue_jsonl_line": 1,
            "first_issue_txt_line": "-",
            "first_mismatch_jsonl_line": "-",
            "first_mismatch_txt_line": "-",
        },
    )
    if not deep_json:
        assert stats.dedup_errors == 1


def test_auditor_contains_json_decoder_recursion_error(capture, monkeypatch):
    capture.write(b"{}\n")
    formatter = ItemFormatter()

    def fail(value):
        raise RecursionError("private decoder detail")

    monkeypatch.setattr("chat_downloader.output.capture_parity.json.loads", fail)
    stats = audit_capture(
        capture.jsonl, capture.txt, formatter=formatter, format_name="default"
    )
    assert stats.failed
    assert (
        stats.jsonl_records,
        stats.jsonl_errors,
        stats.first_issue,
        stats.first_issue_jsonl_line,
        stats.comparison_skipped,
    ) == (0, 1, "jsonl_invalid_json", 1, 1)


@pytest.mark.parametrize("jsonl_newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("txt_newline", [b"\n", b"\r\n"])
def test_origin_newlines_and_sanitized_content(capture, jsonl_newline, txt_newline):
    capture.format("{message}")
    message = "cr\rnel\x85ls\u2028ps\u2029end"
    capture.write(
        json.dumps(_message(message)).encode() + jsonl_newline,
        b"cr\\rnel\\u0085ls\\u2028ps\\u2029end" + txt_newline,
    )
    mismatch = jsonl_newline != txt_newline
    counters = {"newline_style_mismatch": "yes" if mismatch else "no"}
    counters.update(
        {"first_issue": "capture_newline_style_mismatch"}
        if mismatch
        else {"newline_errors": 0}
    )
    capture.check(int(mismatch), counters=counters, absent=(message,))


@pytest.mark.parametrize(
    ("create_jsonl", "create_txt"), [(False, False), (True, True), (True, False)]
)
def test_empty_and_lazy_missing_artifacts(capture, create_jsonl, create_txt):
    for create, path in ((create_jsonl, capture.jsonl), (create_txt, capture.txt)):
        if create:
            path.write_bytes(b"")
    capture.check(
        counters={
            "jsonl_records": 0,
            "txt_lines": 0,
            "jsonl_trailing_newline": "empty",
            "txt_trailing_newline": "empty",
            "jsonl_missing": "no" if create_jsonl else "yes",
            "txt_missing": "no" if create_txt else "yes",
        }
    )


@pytest.mark.parametrize("mode", ["eviction", "zero", "reset"])
def test_production_dedup_limits_and_appended_runs(capture, mode):
    capture.format("{message_type}:{message}")
    kinds = ["paid_message", "ticker_paid_message_item", "paid_message"]
    ids = ["same"] * 3
    if mode == "eviction":
        kinds = ["paid_message", "paid_message", "ticker_paid_message_item"]
        ids = ["one", "two", "one"]
    records = [
        _message(letter, message_type=kind, message_id=key)
        for letter, kind, key in zip("abc", kinds, ids, strict=True)
    ]
    options = {"max_seen_message_ids": 1 if mode == "eviction" else 0}
    expected = records
    if mode == "zero":
        records = records[:2]
        expected = records[:1]
    elif mode == "reset":
        options = {"dedup_reset_before_lines": (2, 3)}
    capture.write(
        records, "".join(f"{r['message_type']}:{r['message']}\n" for r in expected)
    )
    counters = {"suppressed_duplicates": int(mode == "zero")}
    if mode == "eviction":
        counters["expected_txt_lines"] = 3
    if mode == "reset":
        counters["dedup_resets_applied"] = 2
    stats = capture.check(counters=counters, **options)
    assert stats.suppressed_duplicates == int(mode == "zero")
    if mode == "reset":
        assert stats.dedup_resets_applied == 2


@pytest.mark.parametrize("configuration", ["unknown", "missing", "invalid"])
def test_format_configuration_errors(capture, configuration):
    capture.write([])
    capture.format_name = "unknown"
    if configuration != "unknown":
        capture.format_name = "audit"
        capture.format_file = capture.jsonl.parent / f"{configuration}.json"
        if configuration == "invalid":
            capture.format_file.write_text("not-json-private", encoding="utf-8")
    result = capture.run()
    assert result.returncode == 2
    assert result.stdout.startswith("ERROR kind=")
    assert "private" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "kind",
    [
        "directory",
        "dangling",
        "hardlink",
        "symlink",
        pytest.param(
            "fifo",
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"), reason="named FIFOs are unavailable"
            ),
        ),
        pytest.param(
            "permission",
            marks=pytest.mark.skipif(
                os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
                reason="POSIX owner permissions require a non-root test process",
            ),
        ),
    ],
)
def test_input_io_identity_and_nonblocking_errors(capture, kind):
    capture.jsonl = capture.jsonl.parent / "private-capture.jsonl"
    capture.txt.write_bytes(b"")
    if kind == "directory":
        capture.jsonl.mkdir()
    elif kind == "dangling":
        capture.jsonl.symlink_to(capture.jsonl.parent / "missing-target")
    elif kind == "fifo":
        os.mkfifo(capture.jsonl)
    else:
        capture.jsonl.write_bytes(b"{}\n")
        if kind == "permission":
            capture.jsonl.chmod(0)
        else:
            capture.txt.unlink()
            if kind == "hardlink":
                os.link(capture.jsonl, capture.txt)
            else:
                capture.txt.symlink_to(capture.jsonl)
    try:
        result = capture.run(timeout=2.0)
        error = PermissionError if kind == "permission" else OSError
        with pytest.raises(
            error, match="not a regular file" if kind == "fifo" else None
        ):
            capture.direct()
    finally:
        if kind == "permission":
            capture.jsonl.chmod(0o600)
    assert result.returncode == 2
    expected = "input_identity" if kind in {"hardlink", "symlink"} else "input_io"
    assert result.stdout == f"ERROR kind={expected}\n"
    assert result.stderr == ""
    assert "private" not in result.stdout + result.stderr


def test_out_of_range_dedup_reset(capture):
    capture.write([])
    stats = capture.direct(dedup_reset_before_lines=(1,))
    assert stats.failed
    assert not stats.comparison_complete
    assert stats.dedup_reset_errors == 1
    assert stats.first_issue == "dedup_reset_out_of_range"


@pytest.mark.parametrize(
    "options",
    [
        (),
        ("--format", "default", "--max-seen-message-ids", "-1"),
        ("--format", "default", "--private-option", "private-value"),
        (
            "--format",
            "default",
            "--dedup-reset-before-jsonl-line",
            "3",
            "--dedup-reset-before-jsonl-line",
            "2",
        ),
    ],
)
def test_argument_errors_are_content_free(options):
    result = _run_raw("private-jsonl", "private-txt", *options)
    assert result.returncode == 2
    assert result.stdout == "ERROR kind=invalid_arguments\n"
    assert result.stderr == ""


def test_auditor_streams_a_large_capture(capture):
    count = 2_500
    capture.write(
        [_message(f"message-{i}") for i in range(count)],
        "".join(f": message-{i}\n" for i in range(count)),
    )
    result = capture.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"jsonl_records={count}" in result.stdout
    assert f"txt_lines={count}" in result.stdout

# SPDX-License-Identifier: MIT

"""Per-record flush + interval fsync behavior in ContinuousFileWriter."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import chat_downloader.output.writers as writers_module
from chat_downloader.output.continuous_write import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.output.writers import _FSYNC_INTERVAL_SECONDS
from chat_downloader.utils import _bounded_records
from chat_downloader.utils._bounded_records import CaptureRecordLimitError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("writer_class", "items"),
    [
        (JsonLinesContinuousWriter, [{"i": i} for i in range(5)]),
        (TextContinuousWriter, [f"line-{i}" for i in range(3)]),
    ],
)
def test_flushes_each_record(tmp_path, writer_class, items):
    path = tmp_path / "out"
    writer = writer_class(str(path))
    try:
        for i, item in enumerate(items):
            writer.write(item)
            # An independent reader must see bytes, not just Python buffers.
            with path.open(encoding="utf-8") as file:
                assert sum(1 for _ in file) == i + 1
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("initial", "items", "expected"),
    [
        ("existing", ["next"], "existing\nnext\n"),
        ("", [], ""),
        ("existing\n", [], "existing\n"),
        (None, [], ""),
    ],
)
def test_txt_append(tmp_path, initial, items, expected):
    path = tmp_path / "out.txt"
    if initial is not None:
        path.write_text(initial, encoding="utf-8")
    writer = TextContinuousWriter(str(path), overwrite=False)
    try:
        for item in items:
            writer.write(item)
    finally:
        writer.close()
    assert path.read_text(encoding="utf-8") == expected


def test_fsync_runs_at_most_once_per_interval(tmp_path: Path) -> None:
    writer = JsonLinesContinuousWriter(str(tmp_path / "out.jsonl"))
    try:
        with patch.object(os, "fsync") as mock_fsync:
            for count, item in enumerate(({"first": True}, {"again": True}), 1):
                writer._last_fsync_monotonic = (
                    time.monotonic() - _FSYNC_INTERVAL_SECONDS - 1
                )
                writer.write(item)
                assert mock_fsync.call_count == count
                if count == 1:
                    for _ in range(5):
                        writer.write({"more": True})
                    assert mock_fsync.call_count == count
    finally:
        writer.close()


def test_fsync_failure_is_propagated(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        writer._last_fsync_monotonic = time.monotonic() - _FSYNC_INTERVAL_SECONDS - 1
        with (
            patch.object(os, "fsync", side_effect=OSError("nope")),
            pytest.raises(OSError, match="nope"),
        ):
            writer.write({"x": 1})
        writer.write({"x": 2})  # Subsequent writes still work.
    finally:
        writer.close()
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"x": 1},
        {"x": 2},
    ]


@pytest.mark.parametrize(
    ("initial", "item", "expected"),
    [
        (b'{"ok": 1}\n{"partial":', {"ok": 2}, [{"ok": 1}, {"ok": 2}]),
        (b"", {"ok": 1}, [{"ok": 1}]),
        (b'{"ok": 1}', {"ok": 2}, [{"ok": 1}, {"ok": 2}]),
    ],
)
def test_jsonl_append_tail_repair(tmp_path, initial, item, expected):
    path = tmp_path / "out.jsonl"
    path.write_bytes(initial)
    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write(item)
    writer.close()
    assert [json.loads(line) for line in path.read_text().splitlines()] == expected


def test_jsonl_append_tail_recovery_is_stable_for_large_log(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    path.write_text("".join(f'{{"i": {i}}}\n' for i in range(20_000)))
    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"i": 20_000})
    writer.close()
    with path.open() as file:
        assert sum(1 for _line in file) == 20_001


@pytest.mark.parametrize("prefix", [b"", b"{}\n" * 20_000])
@pytest.mark.parametrize("terminal_cr", [b"", b"\r"])
def test_jsonl_append_accepts_exact_record_limit(
    tmp_path, monkeypatch, prefix, terminal_cr
):
    limit = 128
    monkeypatch.setattr(writers_module, "MAX_CAPTURE_RECORD_BYTES", limit)
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", limit)
    monkeypatch.setattr(writers_module, "_TAIL_SCAN_BYTES", 8)
    header, footer = b'{"message":"', b'"}'
    tail = header + b"x" * (limit - len(header) - len(footer)) + footer
    path = tmp_path / "exact.jsonl"
    path.write_bytes(prefix + tail + terminal_cr)

    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"next": True})
    writer.close()

    newline = b"\n" if prefix else b"\r\n" if terminal_cr else os.linesep.encode()
    assert path.read_bytes() == prefix + tail + newline + b'{"next": true}' + newline


@pytest.mark.parametrize("host_newline", ["\n", "\r\n"])
@pytest.mark.parametrize("origin_newline", ["\n", "\r\n"])
@pytest.mark.parametrize("tail", ["terminated", "unterminated", "partial_cr"])
@pytest.mark.parametrize("kind", ["jsonl", "txt"])
def test_append_preserves_origin_newlines(
    tmp_path, monkeypatch, host_newline, origin_newline, tail, kind
):
    monkeypatch.setattr(os, "linesep", host_newline)
    monkeypatch.setattr(writers_module, "_TAIL_SCAN_BYTES", 8)
    records = [{"message": word} for word in ("first", "second", "third")]
    lines = [
        json.dumps(record) if kind == "jsonl" else record["message"]
        for record in records
    ]
    newline = origin_newline.encode()
    ending = newline if tail == "terminated" else b"\r" if tail == "partial_cr" else b""
    path = tmp_path / f"capture.{kind}"
    path.write_bytes(lines[0].encode() + newline + lines[1].encode() + ending)
    factory = JsonLinesContinuousWriter if kind == "jsonl" else TextContinuousWriter
    writer = factory(str(path), overwrite=False)
    try:
        writer.write(records[2] if kind == "jsonl" else lines[2])
    finally:
        writer.close()
    assert path.read_bytes() == newline.join(line.encode() for line in lines) + newline


@pytest.mark.parametrize("host_newline", ["\n", "\r\n"])
@pytest.mark.parametrize("kind", ["jsonl", "txt"])
def test_first_unterminated_record_uses_host_newline(
    tmp_path, monkeypatch, host_newline, kind
):
    monkeypatch.setattr(os, "linesep", host_newline)
    path = tmp_path / f"capture.{kind}"
    initial = '{"message": "first"}' if kind == "jsonl" else "first"
    appended = {"message": "second"} if kind == "jsonl" else "second"
    path.write_bytes(initial.encode())
    factory = JsonLinesContinuousWriter if kind == "jsonl" else TextContinuousWriter
    writer = factory(str(path), overwrite=False)
    try:
        writer.write(appended)
    finally:
        writer.close()
    final = json.dumps(appended) if kind == "jsonl" else appended
    assert path.read_bytes() == (initial + host_newline + final + host_newline).encode()


@pytest.mark.parametrize("origin_newline", [b"\n", b"\r\n"])
def test_truncated_jsonl_tail_keeps_prefix_newline(tmp_path, origin_newline):
    path = tmp_path / "capture.jsonl"
    prefix = b'{"message": "first"}' + origin_newline
    path.write_bytes(prefix + b'{"message": "truncated')
    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    try:
        writer.write({"message": "second"})
    finally:
        writer.close()
    assert path.read_bytes() == prefix + b'{"message": "second"}' + origin_newline


@pytest.mark.parametrize("prefix", [b"", b"{}\n" * 20_000])
@pytest.mark.parametrize("terminal_cr", [b"", b"\r"])
def test_jsonl_append_rejects_one_byte_excess_without_repair_or_handle_leak(
    tmp_path, monkeypatch, prefix, terminal_cr
):
    limit = 128
    monkeypatch.setattr(writers_module, "MAX_CAPTURE_RECORD_BYTES", limit)
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", limit)
    monkeypatch.setattr(writers_module, "_TAIL_SCAN_BYTES", 8)
    header, footer = b'{"private":"', b'"}'
    tail = header + b"x" * (limit + 1 - len(header) - len(footer)) + footer
    original = prefix + tail + terminal_cr
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(original)
    opened, read_sizes = [], []
    detach = writers_module._OutputFile.detach

    def track_detach(prepared):
        stream = detach(prepared)
        opened.append(stream)
        read = stream.read

        def bounded_read(size=-1):
            assert 0 <= size <= 8
            read_sizes.append(size)
            return read(size)

        monkeypatch.setattr(stream, "read", bounded_read)
        return stream

    monkeypatch.setattr(writers_module._OutputFile, "detach", track_detach)
    with pytest.raises(CaptureRecordLimitError, match="128-byte limit") as caught:
        JsonLinesContinuousWriter(str(path), overwrite=False)

    assert "private" not in str(caught.value)
    assert path.read_bytes() == original
    assert opened
    assert all(stream.closed for stream in opened)
    assert sum(read_sizes) <= limit + len(terminal_cr) + 2


def test_jsonl_tail_read_error_preserves_records_and_closes_input(
    tmp_path, monkeypatch
):
    path = tmp_path / "unreadable-tail.jsonl"
    original = b'{"kept":true}\n{"partial":'
    path.write_bytes(original)
    opened = []
    detach = writers_module._OutputFile.detach

    def fail_tail_read(prepared):
        stream = detach(prepared)
        opened.append(stream)
        read = stream.read

        def fail_scan(size=-1):
            if size != 1:
                raise OSError("tail read failed")
            return read(size)

        monkeypatch.setattr(stream, "read", fail_scan)
        return stream

    monkeypatch.setattr(writers_module._OutputFile, "detach", fail_tail_read)
    with pytest.raises(OSError, match="tail read failed"):
        JsonLinesContinuousWriter(str(path), overwrite=False)
    assert path.read_bytes() == original
    assert opened
    assert all(stream.closed for stream in opened)

# SPDX-License-Identifier: MIT

"""Per-record flush + interval fsync behavior in ContinuousFileWriter."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from chat_downloader.output.continuous_write import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.output.writers import _FSYNC_INTERVAL_SECONDS

if TYPE_CHECKING:
    from pathlib import Path


def _read_lines_during_write(writer_path: Path) -> int:
    """Count complete lines visible to a second independent reader.

    Opens the file independently after a write to verify bytes reached
    the OS, not just Python buffers.
    """
    with open(writer_path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def test_jsonl_flushes_each_record(tmp_path: Path) -> None:
    """A second reader can see every record immediately after write()."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        for i in range(5):
            writer.write({"i": i})
            assert _read_lines_during_write(path) == i + 1
    finally:
        writer.close()


def test_txt_flushes_each_record(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    writer = TextContinuousWriter(str(path))
    try:
        for i in range(3):
            writer.write(f"line-{i}")
            assert _read_lines_during_write(path) == i + 1
    finally:
        writer.close()


def test_txt_append_terminates_existing_line_missing_newline(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    path.write_text("existing", encoding="utf-8")

    writer = TextContinuousWriter(str(path), overwrite=False)
    writer.write("next")
    writer.close()

    assert path.read_text(encoding="utf-8") == "existing\nnext\n"


@pytest.mark.parametrize("initial", ["", "existing\n"])
def test_txt_append_keeps_complete_file_unchanged(tmp_path: Path, initial: str) -> None:
    path = tmp_path / "out.txt"
    path.write_text(initial, encoding="utf-8")

    writer = TextContinuousWriter(str(path), overwrite=False)
    writer.close()

    assert path.read_text(encoding="utf-8") == initial


def test_txt_append_creates_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"

    writer = TextContinuousWriter(str(path), overwrite=False)
    writer.close()

    assert path.read_text(encoding="utf-8") == ""


def test_fsync_runs_at_most_once_per_interval(tmp_path: Path) -> None:
    """fsync() runs on first write, then is suppressed until the interval."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        with patch.object(os, "fsync") as mock_fsync:
            # Force the timer back so the first write triggers fsync.
            writer._last_fsync_monotonic = (
                time.monotonic() - _FSYNC_INTERVAL_SECONDS - 1
            )
            writer.write({"first": True})
            assert mock_fsync.call_count == 1

            # Subsequent writes inside the interval must NOT fsync.
            for _ in range(5):
                writer.write({"more": True})
            assert mock_fsync.call_count == 1

            # Force the timer back again; next write fsyncs once.
            writer._last_fsync_monotonic = (
                time.monotonic() - _FSYNC_INTERVAL_SECONDS - 1
            )
            writer.write({"again": True})
            assert mock_fsync.call_count == 2
    finally:
        writer.close()


def test_fsync_failure_is_propagated(tmp_path: Path) -> None:
    """A durability failure must stop the run instead of hiding data loss."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        writer._last_fsync_monotonic = time.monotonic() - _FSYNC_INTERVAL_SECONDS - 1
        with (
            patch.object(os, "fsync", side_effect=OSError("nope")),
            pytest.raises(OSError, match="nope"),
        ):
            writer.write({"x": 1})
        # Subsequent writes still work.
        writer.write({"x": 2})
    finally:
        writer.close()

    with open(path, encoding="utf-8") as f:
        records: list[Any] = [json.loads(line) for line in f]
    assert records == [{"x": 1}, {"x": 2}]


def test_jsonl_append_removes_crash_truncated_record(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    path.write_bytes(b'{"ok": 1}\n{"partial":')

    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"ok": 2})
    writer.close()

    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"ok": 1},
        {"ok": 2},
    ]


def test_jsonl_append_to_empty_file_needs_no_tail_repair(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.touch()

    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"ok": 1})
    writer.close()

    assert json.loads(path.read_text()) == {"ok": 1}


def test_jsonl_append_terminates_valid_record_missing_newline(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    path.write_bytes(b'{"ok": 1}')

    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"ok": 2})
    writer.close()

    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"ok": 1},
        {"ok": 2},
    ]


def test_jsonl_append_tail_recovery_is_stable_for_large_log(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    path.write_text("".join(f'{{"i": {i}}}\n' for i in range(20_000)))

    writer = JsonLinesContinuousWriter(str(path), overwrite=False)
    writer.write({"i": 20_000})
    writer.close()

    with path.open() as file:
        assert sum(1 for _line in file) == 20_001

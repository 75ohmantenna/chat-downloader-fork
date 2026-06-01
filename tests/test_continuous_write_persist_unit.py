# SPDX-License-Identifier: MIT

"""Per-record flush + interval fsync behavior in ContinuousFileWriter."""

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from chat_downloader.output import continuous_write
from chat_downloader.output.continuous_write import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)


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


def test_fsync_runs_at_most_once_per_interval(tmp_path: Path) -> None:
    """fsync() runs on first write, then is suppressed until the interval."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        with patch.object(os, "fsync") as mock_fsync:
            # Force the timer back so the first write triggers fsync.
            writer._last_fsync_monotonic = (
                time.monotonic() - continuous_write._FSYNC_INTERVAL_SECONDS - 1
            )
            writer.write({"first": True})
            assert mock_fsync.call_count == 1

            # Subsequent writes inside the interval must NOT fsync.
            for _ in range(5):
                writer.write({"more": True})
            assert mock_fsync.call_count == 1

            # Force the timer back again; next write fsyncs once.
            writer._last_fsync_monotonic = (
                time.monotonic() - continuous_write._FSYNC_INTERVAL_SECONDS - 1
            )
            writer.write({"again": True})
            assert mock_fsync.call_count == 2
    finally:
        writer.close()


def test_fsync_failure_is_swallowed(tmp_path: Path) -> None:
    """OSError from fsync must not propagate; data path keeps going."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        writer._last_fsync_monotonic = (
            time.monotonic() - continuous_write._FSYNC_INTERVAL_SECONDS - 1
        )
        with patch.object(os, "fsync", side_effect=OSError("nope")):
            writer.write({"x": 1})  # must not raise
        # Subsequent writes still work.
        writer.write({"x": 2})
    finally:
        writer.close()

    with open(path, encoding="utf-8") as f:
        records: list[Any] = [json.loads(line) for line in f]
    assert records == [{"x": 1}, {"x": 2}]

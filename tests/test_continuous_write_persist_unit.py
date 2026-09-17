# SPDX-License-Identifier: MIT

"""Per-record flush + interval fsync behavior in ContinuousFileWriter."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from chat_downloader.output.continuous_write import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.output.writers import _FSYNC_INTERVAL_SECONDS

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

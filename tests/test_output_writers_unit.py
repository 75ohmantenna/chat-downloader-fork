# SPDX-License-Identifier: MIT

"""JSONL records, persistence, and rejected legacy output extensions."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader.output.continuous_write import (
    ContinuousWriter,
    JsonLinesContinuousWriter,
)


@pytest.mark.parametrize(
    "writer_class",
    [JsonLinesContinuousWriter, ContinuousWriter],
)
@pytest.mark.parametrize(
    "items",
    [
        [{"message": "test", "id": 99}],
        [{"a": 1, "b": "hello"}, {"x": [1, 2, 3]}, {"z": None}],
    ],
)
def test_jsonl_records_round_trip(jsonl_path: str, writer_class, items) -> None:
    writer = writer_class(jsonl_path, sort_keys=True)
    try:
        for item in items:
            writer.write(item)
    finally:
        writer.close()
    assert [
        json.loads(line) for line in Path(jsonl_path).read_text().splitlines()
    ] == items


@pytest.mark.parametrize(
    ("sort_keys", "expected"),
    [(True, '{"a": 1, "m": 2, "z": 3}'), (False, '{"z": 3, "a": 1, "m": 2}')],
)
def test_jsonl_sort_keys_applied(jsonl_path, sort_keys, expected):
    with ContinuousWriter(jsonl_path, sort_keys=sort_keys) as writer:
        writer.write({"z": 3, "a": 1, "m": 2})
    assert Path(jsonl_path).read_text() == expected + "\n"


def test_writer_flush_and_periodic_fsync_paths(jsonl_path: str) -> None:
    writer = JsonLinesContinuousWriter(jsonl_path)
    writer.flush()  # base flush guard passes: file is open
    writer.file = Mock()
    writer.file.fileno.side_effect = ValueError("no fd")
    writer._last_fsync_monotonic = float("-inf")
    writer._persist_after_write()  # flush succeeds, fsync failure is logged
    writer.file.fileno.side_effect = None
    writer._last_fsync_monotonic = float("-inf")
    writer._persist_after_write()  # real fsync path executes
    writer.file = None
    writer.flush()  # file None: base flush guard is a no-op
    writer._persist_after_write()  # Missing file must also skip persistence.


def test_jsonl_overwrite_true_truncates_existing_file(jsonl_path: str) -> None:
    path = Path(jsonl_path)
    path.write_text('{"stale": true}\n', encoding="utf-8")
    writer = JsonLinesContinuousWriter(jsonl_path, overwrite=True)
    writer.write({"fresh": 1})
    writer.close()
    assert path.read_text(encoding="utf-8").splitlines() == ['{"fresh": 1}']


def test_close_logs_fsync_skip_for_handle_without_fd() -> None:
    writer = JsonLinesContinuousWriter.__new__(JsonLinesContinuousWriter)
    writer.file = io.StringIO()
    writer.file_name = "unused.jsonl"
    writer.close()  # StringIO has no fileno: fsync skip logged, close runs
    assert writer.file is None


def test_json_extension_rejected_with_jsonl_message(tmp_path):
    path = tmp_path / "test.json"
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(str(path))

    assert not path.exists()

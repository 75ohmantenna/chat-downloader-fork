# SPDX-License-Identifier: MIT

"""Unit tests for continuous output writers.

Covers:
- JSONL: one JSON object per line, direct write path
- JSON extension: rejected with a clear JSONL migration error
"""

import json
from unittest.mock import MagicMock

import pytest

from chat_downloader.output.continuous_write import (
    ContinuousWriter,
    JsonLinesContinuousWriter,
)

# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------


def test_jsonl_three_items_three_lines(jsonl_path: str) -> None:
    items = [{"a": 1, "b": "hello"}, {"x": [1, 2, 3]}, {"z": None}]
    w = JsonLinesContinuousWriter(jsonl_path, sort_keys=True)
    for item in items:
        w.write(item)
    w.close()

    with open(jsonl_path, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln]

    assert len(lines) == 3
    for original, line in zip(items, lines, strict=True):
        assert json.loads(line) == original


def test_jsonl_single_item(jsonl_path: str) -> None:
    item = {"message": "test", "id": 99}
    w = JsonLinesContinuousWriter(jsonl_path, sort_keys=True)
    w.write(item)
    w.close()

    with open(jsonl_path, encoding="utf-8") as fh:
        assert json.loads(fh.read().strip()) == item


def test_jsonl_uses_direct_write_not_print(jsonl_path: str) -> None:
    """Verify write() uses file.write(), not print().

    Per-record flush is always on (live captures need to survive SIGKILL),
    so we assert write() ran and don't constrain flush() beyond "at least
    once".
    """
    w = JsonLinesContinuousWriter(jsonl_path, sort_keys=True)
    mock_file = MagicMock()
    w.file = mock_file

    w.write({"k": "v"})

    mock_file.write.assert_called_once()
    assert mock_file.flush.call_count >= 1


def test_jsonl_flush_when_requested(jsonl_path: str) -> None:
    """flush=True forces an explicit flush in addition to the implicit one."""
    w = JsonLinesContinuousWriter(jsonl_path, sort_keys=True)
    mock_file = MagicMock()
    w.file = mock_file

    w.write({"k": "v"}, flush=True)

    mock_file.write.assert_called_once()
    # Implicit flush (per-record persistence) + explicit flush (flush=True).
    assert mock_file.flush.call_count == 2


def test_jsonl_sort_keys_applied(jsonl_path: str) -> None:
    w = JsonLinesContinuousWriter(jsonl_path, sort_keys=True)
    w.write({"z": 3, "a": 1, "m": 2})
    w.close()

    with open(jsonl_path, encoding="utf-8") as fh:
        line = fh.readline().strip()

    assert line == '{"a": 1, "m": 2, "z": 3}'


def test_jsonl_via_continuous_writer_factory(jsonl_path: str) -> None:
    """ContinuousWriter factory selects JsonLinesContinuousWriter for .jsonl."""
    w = ContinuousWriter(jsonl_path)
    w.write({"hello": "world"})
    w.close()

    with open(jsonl_path, encoding="utf-8") as fh:
        assert json.loads(fh.readline()) == {"hello": "world"}


def test_jsonl_overwrite_true_truncates_existing_file(jsonl_path: str) -> None:
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        fh.write('{"stale": true}\n')

    writer = JsonLinesContinuousWriter(jsonl_path, overwrite=True)
    writer.write({"fresh": 1})
    writer.close()

    with open(jsonl_path, encoding="utf-8") as fh:
        lines = [line.rstrip("\n") for line in fh]

    assert lines == ['{"fresh": 1}']


def test_json_extension_rejected_with_jsonl_message(
    tmp_path: pytest.TempPathFactory,
) -> None:
    path = tmp_path / "test.json"

    with pytest.raises(ValueError, match=r"Use a \.jsonl output path"):
        ContinuousWriter(str(path))

    assert not path.exists()

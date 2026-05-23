# SPDX-License-Identifier: MIT

"""Unit tests for continuous output writers.

Covers:
- JSONL: one JSON object per line, direct write path
- JSON array: finalize-on-close (no per-message seek/backpatch)
- JSON array: empty file produces valid ``[]``
- JSON array: overwrite=False replays existing items
- JSON array writer does NOT call seek() during write()
"""

import json
import pathlib
from typing import NoReturn
from unittest.mock import MagicMock

from chat_downloader.output.continuous_write import (
    ContinuousWriter,
    JsonContinuousWriter,
    JsonLinesContinuousWriter,
)


def _jsonl_path(tmp_path: pathlib.Path) -> str:
    return str(tmp_path / "test.jsonl")


def _json_path(tmp_path: pathlib.Path) -> str:
    path = tmp_path / "test.json"
    path.touch()
    return str(path)


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------


def test_jsonl_three_items_three_lines(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    items = [{"a": 1, "b": "hello"}, {"x": [1, 2, 3]}, {"z": None}]
    w = JsonLinesContinuousWriter(path, sort_keys=True)
    for item in items:
        w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln]

    assert len(lines) == 3
    for original, line in zip(items, lines, strict=True):
        assert json.loads(line) == original


def test_jsonl_single_item(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    item = {"message": "test", "id": 99}
    w = JsonLinesContinuousWriter(path, sort_keys=True)
    w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.loads(fh.read().strip()) == item


def test_jsonl_uses_direct_write_not_print(tmp_path: pathlib.Path) -> None:
    """Verify write() uses file.write(), not print()."""
    w = JsonLinesContinuousWriter(_jsonl_path(tmp_path), sort_keys=True)
    mock_file = MagicMock()
    w.file = mock_file

    w.write({"k": "v"})

    mock_file.write.assert_called_once()
    mock_file.flush.assert_not_called()


def test_jsonl_flush_when_requested(tmp_path: pathlib.Path) -> None:
    w = JsonLinesContinuousWriter(_jsonl_path(tmp_path), sort_keys=True)
    mock_file = MagicMock()
    w.file = mock_file

    w.write({"k": "v"}, flush=True)

    mock_file.write.assert_called_once()
    mock_file.flush.assert_called_once()


def test_jsonl_sort_keys_applied(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    w = JsonLinesContinuousWriter(path, sort_keys=True)
    w.write({"z": 3, "a": 1, "m": 2})
    w.close()

    with open(path, encoding="utf-8") as fh:
        line = fh.readline().strip()

    assert line == '{"a": 1, "m": 2, "z": 3}'


def test_jsonl_via_continuous_writer_factory(tmp_path: pathlib.Path) -> None:
    """ContinuousWriter factory selects JsonLinesContinuousWriter for .jsonl."""
    path = _jsonl_path(tmp_path)
    w = ContinuousWriter(path)
    w.write({"hello": "world"})
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.loads(fh.readline()) == {"hello": "world"}


def test_jsonl_overwrite_true_truncates_existing_file(
    tmp_path: pathlib.Path,
) -> None:
    path = _jsonl_path(tmp_path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"stale": true}\n')

    writer = JsonLinesContinuousWriter(path, overwrite=True)
    writer.write({"fresh": 1})
    writer.close()

    with open(path, encoding="utf-8") as fh:
        lines = [line.rstrip("\n") for line in fh]

    assert lines == ['{"fresh": 1}']


# ---------------------------------------------------------------------------
# JSON array writer
# ---------------------------------------------------------------------------


def test_json_two_items_valid_array(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    items = [{"id": 1, "msg": "hello"}, {"id": 2, "msg": "world"}]
    w = JsonContinuousWriter(path)
    for item in items:
        w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == items[0]
    assert result[1] == items[1]


def test_json_empty_close_produces_valid_empty_array(
    tmp_path: pathlib.Path,
) -> None:
    path = _json_path(tmp_path)
    w = JsonContinuousWriter(path)
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == []


def test_json_close_is_idempotent(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    w = JsonContinuousWriter(path)
    w.write({"id": 1})
    w.close()
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == [{"id": 1}]


def test_json_single_item(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    item = {"key": "value"}
    w = JsonContinuousWriter(path)
    w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == [item]


def test_json_many_items_roundtrip(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    items = [{"n": i} for i in range(50)]
    w = JsonContinuousWriter(path)
    for item in items:
        w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == items


def test_json_write_does_not_call_seek(tmp_path: pathlib.Path) -> None:
    """Seek() must never be called inside write() — no backpatch."""
    path = _json_path(tmp_path)
    w = JsonContinuousWriter(path)

    real_file = w.file
    mock_file = MagicMock(wraps=real_file)

    def _no_seek(*args, **kwargs) -> NoReturn:
        msg = "seek() was called inside write() — backpatch detected"
        raise AssertionError(msg)

    mock_file.seek = _no_seek
    w.file = mock_file

    try:
        w.write({"first": True})
        w.write({"second": True})
    finally:
        w.file = real_file
        w.close()


def test_json_indented_output_is_valid_json(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    items = [{"a": 1}, {"b": 2}]
    w = JsonContinuousWriter(path, indent=2)
    for item in items:
        w.write(item)
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == items


def test_json_overwrite_false_preserves_previous_items(
    tmp_path: pathlib.Path,
) -> None:
    """Writing with overwrite=False should include items from prior session."""
    path = _json_path(tmp_path)

    w1 = JsonContinuousWriter(path, overwrite=True)
    w1.write({"session": 1, "n": 0})
    w1.write({"session": 1, "n": 1})
    w1.close()

    w2 = JsonContinuousWriter(path, overwrite=False)
    w2.write({"session": 2, "n": 0})
    w2.close()

    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)

    assert len(result) == 3
    assert result[2]["session"] == 2


def test_json_via_continuous_writer_factory(tmp_path: pathlib.Path) -> None:
    """ContinuousWriter factory selects JsonContinuousWriter for .json."""
    path = _json_path(tmp_path)
    w = ContinuousWriter(path)
    w.write({"x": 42})
    w.close()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == [{"x": 42}]


def test_json_sort_keys_applied(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    w = JsonContinuousWriter(path, sort_keys=True)
    w.write({"z": 1, "a": 2})
    w.close()

    with open(path, encoding="utf-8") as fh:
        raw = fh.read()

    assert raw.index('"a"') < raw.index('"z"')

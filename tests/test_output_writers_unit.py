# SPDX-License-Identifier: MIT

"""Unit tests for continuous output writers.

Covers:
- JSONL: one JSON object per line, direct write path
- JSON extension: rejected with a clear JSONL migration error
"""

from __future__ import annotations

import json

import pytest

from chat_downloader.output.continuous_write import (
    ContinuousWriter,
    JsonLinesContinuousWriter,
)


@pytest.mark.parametrize("writer_class", [JsonLinesContinuousWriter, ContinuousWriter])
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

    with open(jsonl_path, encoding="utf-8") as file:
        assert [json.loads(line) for line in file] == items


@pytest.mark.parametrize(
    ("sort_keys", "expected"),
    [(True, '{"a": 1, "m": 2, "z": 3}'), (False, '{"z": 3, "a": 1, "m": 2}')],
)
def test_jsonl_sort_keys_applied(
    jsonl_path: str, sort_keys: bool, expected: str
) -> None:
    with ContinuousWriter(jsonl_path, sort_keys=sort_keys) as writer:
        writer.write({"z": 3, "a": 1, "m": 2})
    with open(jsonl_path, encoding="utf-8") as file:
        assert file.read() == expected + "\n"


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

    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(str(path))

    assert not path.exists()

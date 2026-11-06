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
    TextContinuousWriter,
)


@pytest.fixture(
    params=[
        (ContinuousWriter, "jsonl", {"lazy_initialise": False}),
        (ContinuousWriter, "jsonl", {"lazy_initialise": True}),
        (ContinuousWriter, "txt", {"lazy_initialise": False}),
        (ContinuousWriter, "txt", {"lazy_initialise": True}),
        (JsonLinesContinuousWriter, "jsonl", {}),
        (TextContinuousWriter, "txt", {}),
    ],
    ids=["jsonl-eager", "jsonl-lazy", "txt-eager", "txt-lazy", "jsonl", "txt"],
)
def writer_configuration(request):
    return request.param


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("tail", [b'{"preserved": true}', b'{"partial":'])
def test_unknown_writer_option_preserves_existing_output(
    tmp_path, writer_configuration, overwrite, tail
):
    writer_class, extension, options = writer_configuration
    path = tmp_path / f"capture.{extension}"
    original = b'{"complete": true}\n' + tail
    path.write_bytes(original)

    with pytest.raises(TypeError, match="over_write"):
        writer_class(str(path), overwrite=overwrite, over_write=False, **options)

    assert path.read_bytes() == original


def test_unknown_writer_option_creates_no_output_or_parent(
    tmp_path, writer_configuration
):
    writer_class, extension, options = writer_configuration
    parent = tmp_path / "missing" / "nested"
    path = parent / f"capture.{extension}"

    with pytest.raises(TypeError, match="over_write"):
        writer_class(str(path), over_write=False, **options)

    assert not path.exists()
    assert not parent.parent.exists()


@pytest.mark.parametrize(
    "writer_class",
    [JsonLinesContinuousWriter, ContinuousWriter],
)
@pytest.mark.parametrize(
    "items",
    [
        [{"message": "test", "id": 99}],
        [{"a": 1, "b": "hello"}, {"x": [1, 2, 3]}, {"z": None}],
        [{"finite": [0, -1, 1.5, 1e308, -1e-308]}, [0, {"nested": 2.5}]],
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


@pytest.mark.parametrize("writer_class", [ContinuousWriter, JsonLinesContinuousWriter])
@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("list_root", [False, True])
def test_jsonl_rejects_nested_nonfinite_without_writing_record(
    tmp_path, writer_class, nonfinite, list_root
):
    path = tmp_path / "capture.jsonl"
    original = '{"existing": "café"}\n'.encode()
    path.write_bytes(original)
    valid = {"finite": [0, -2.5, 1e308]}
    invalid = {"nested": [{"value": nonfinite}]}
    if list_root:
        invalid = [invalid]

    writer = writer_class(str(path), overwrite=False)
    try:
        writer.write(valid)
        preserved = path.read_bytes()
        assert preserved == original + (
            json.dumps(valid, sort_keys=True) + "\n"
        ).encode("utf-8")
        with pytest.raises(ValueError):
            writer.write(invalid)
        assert path.read_bytes() == preserved
        writer.write({"after": 3.25})
    finally:
        writer.close()

    assert path.read_bytes() == preserved + b'{"after": 3.25}\n'
    assert [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ] == [
        {"existing": "café"},
        valid,
        {"after": 3.25},
    ]


@pytest.mark.parametrize(
    ("sort_keys", "expected"),
    [(True, '{"a": 1, "m": 2, "z": 3}'), (False, '{"z": 3, "a": 1, "m": 2}')],
)
@pytest.mark.parametrize("writer_class", [ContinuousWriter, JsonLinesContinuousWriter])
def test_jsonl_sort_keys_applied(jsonl_path, sort_keys, expected, writer_class):
    writer = writer_class(jsonl_path, sort_keys=sort_keys)
    try:
        writer.write({"z": 3, "a": 1, "m": 2})
    finally:
        writer.close()
    assert Path(jsonl_path).read_text() == expected + "\n"


@pytest.mark.parametrize("sort_keys", [False, True])
@pytest.mark.parametrize("lazy", [False, True])
def test_factory_text_output_accepts_json_sort_option(tmp_path, sort_keys, lazy):
    path = tmp_path / "capture.txt"
    with ContinuousWriter(
        str(path),
        sort_keys=sort_keys,
        format="txt",
        lazy_initialise=lazy,
    ) as writer:
        writer.write("preserved text order")
    assert path.read_text() == "preserved text order\n"


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

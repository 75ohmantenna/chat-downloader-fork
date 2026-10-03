from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from chat_downloader.utils import capture_reader
from chat_downloader.utils.capture_reader import (
    CaptureLine,
    open_capture_input,
    read_capture_lines,
)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"key":1,"key":2}',
        b'{"unused":{"key":1,"key":2}}',
        b'{"unused":NaN}',
        b'{"unused":[Infinity]}',
        b'{"unused":-Infinity}',
        b'{"unused":1e9999}',
        rb'{"unused":"\ud800"}',
        rb'{"unused":{"\ud800":0}}',
        b"[" * 2000 + b"0" + b"]" * 2000,
        b'{"unused":"\xff"}',
        b"PRIVATE_SENTINEL",
        b"",
    ],
)
def test_invalid_lines_report_only_position_and_continue(tmp_path, raw):
    path = tmp_path / "capture.jsonl"
    path.write_bytes(raw + b"\n{}\n")

    assert list(read_capture_lines(path)) == [
        CaptureLine(1, None, "invalid_jsonl"),
        CaptureLine(2, {}, None),
    ]


@pytest.mark.parametrize("raw", [b"[]", b"null", b"false", b"1.5", b'"text"'])
def test_non_object_json_has_a_distinct_finding(tmp_path, raw):
    path = tmp_path / "capture.jsonl"
    path.write_bytes(raw + b"\n{}")

    assert list(read_capture_lines(path)) == [
        CaptureLine(1, None, "non_object_record"),
        CaptureLine(2, {}, None),
    ]


def test_valid_objects_preserve_unicode_nested_values_and_physical_lines(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.write_text(
        '{"values":[1,1.5,true,null,{"text":"猫"}]}\r\n{}', encoding="utf-8"
    )

    assert list(read_capture_lines(path)) == [
        CaptureLine(1, {"values": [1, 1.5, True, None, {"text": "猫"}]}, None),
        CaptureLine(2, {}, None),
    ]


def test_empty_capture_has_no_physical_lines(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.touch()
    assert list(read_capture_lines(path)) == []


def test_input_closes_after_consumer_failure(tmp_path):
    path = tmp_path / "capture.jsonl"
    path.write_bytes(b"{}\n")

    with (
        pytest.raises(RuntimeError, match="consumer stopped"),
        open_capture_input(path) as source,
    ):
        assert source.read() == b"{}\n"
        raise RuntimeError("consumer stopped")
    assert source.closed


def test_missing_input_propagates_io_failure(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(read_capture_lines(tmp_path / "missing"))


def test_early_close_releases_input_with_retained_iterator(tmp_path, monkeypatch):
    path = tmp_path / "capture.jsonl"
    path.write_bytes(b"{}\n{}\n")
    opened = []

    @contextmanager
    def track_input(path):
        with open_capture_input(path) as source:
            opened.append(source)
            yield source

    monkeypatch.setattr(capture_reader, "open_capture_input", track_input)
    records = read_capture_lines(path)
    assert next(records) == CaptureLine(1, {}, None)
    assert not opened[0].closed
    records.close()
    assert opened[0].closed
    assert list(records) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file contract")
def test_special_file_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "capture.fifo"
    os.mkfifo(path)
    with pytest.raises(OSError):
        list(read_capture_lines(path))

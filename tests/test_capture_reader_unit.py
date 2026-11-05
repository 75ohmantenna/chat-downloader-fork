from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from importlib import import_module

import pytest

from chat_downloader.utils import _bounded_records, capture_reader
from chat_downloader.utils._bounded_records import CaptureRecordLimitError
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


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b""])
def test_capture_reader_accepts_exact_record_byte_limit(tmp_path, monkeypatch, newline):
    assert _bounded_records.MAX_CAPTURE_RECORD_BYTES == 16 * 1024 * 1024
    limit = 128
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", limit)
    header, footer = b'{"message":"', b'"}'
    message = "x" * (limit - len(header) - len(footer))
    path = tmp_path / "exact.jsonl"
    path.write_bytes(header + message.encode() + footer + newline)

    assert list(read_capture_lines(path)) == [
        CaptureLine(1, {"message": message}, None)
    ]


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b""])
def test_oversized_capture_line_fails_before_parse_and_closes_input(
    tmp_path, monkeypatch, newline
):
    limit = 128
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", limit)
    path = tmp_path / "oversized.jsonl"
    header, footer = b'{"private":"', b'"}'
    # Multibyte UTF-8 counts as bytes, not decoded characters.
    body = header + b"x" * (limit - len(header) - len(footer) - 1)
    body += "é".encode() + footer
    assert len(body) == limit + 1
    path.write_bytes(b"{}\n" + body + newline + (b"{}\n" if newline else b""))
    opened, read_sizes = [], []

    @contextmanager
    def track_input(path):
        with open_capture_input(path) as source:
            opened.append(source)
            readline = source.readline

            def bounded_readline(size=-1):
                assert size == limit + 2
                read_sizes.append(size)
                return readline(size)

            monkeypatch.setattr(source, "readline", bounded_readline)
            yield source

    monkeypatch.setattr(capture_reader, "open_capture_input", track_input)
    records = read_capture_lines(path)
    assert next(records) == CaptureLine(1, {}, None)

    def reject_parse(*args, **kwargs):
        pytest.fail("oversized input reached JSON parsing")

    monkeypatch.setattr(capture_reader.json, "loads", reject_parse)
    with pytest.raises(CaptureRecordLimitError, match="128-byte limit") as caught:
        next(records)

    assert "private" not in str(caught.value)
    assert opened
    assert all(source.closed for source in opened)
    assert len(read_sizes) == 2
    assert list(records) == []


def test_capture_reader_streams_large_total_capture_under_record_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", 128)
    path = tmp_path / "many-records.jsonl"
    path.write_bytes(b'{"ok":true}\n' * 20_000)

    assert sum(line.record == {"ok": True} for line in read_capture_lines(path)) == (
        20_000
    )


@pytest.mark.parametrize("provider", ["youtube", "twitch", "kick"])
def test_inspector_record_limit_error_is_private_and_releases_all_resources(
    tmp_path, monkeypatch, capsys, provider
):
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", 128)
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(b"{}\nPRIVATE_SENTINEL" + b"x" * 128)
    opened, directories, databases = [], [], []
    temporary_directory = tempfile.TemporaryDirectory
    connect = sqlite3.connect

    @contextmanager
    def track_input(path):
        with open_capture_input(path) as source:
            opened.append(source)
            yield source

    @contextmanager
    def track_directory(**kwargs):
        with temporary_directory(dir=tmp_path, **kwargs) as directory:
            directories.append(directory)
            yield directory

    def track_database(*args, **kwargs):
        database = connect(*args, **kwargs)
        databases.append(database)
        return database

    monkeypatch.setattr(capture_reader, "open_capture_input", track_input)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", track_directory)
    monkeypatch.setattr(sqlite3, "connect", track_database)
    inspector = import_module(f"scripts.inspect_{provider}_capture")

    assert inspector.main([str(path)]) == 2

    output = capsys.readouterr()
    assert output.out == '{"error": "input_or_temporary_storage_io"}\n'
    assert output.err == ""
    assert opened
    assert all(source.closed for source in opened)
    assert directories
    assert all(not os.path.exists(path) for path in directories)
    assert len(databases) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        databases[0].execute("SELECT 1")

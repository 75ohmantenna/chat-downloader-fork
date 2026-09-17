# SPDX-License-Identifier: MIT

"""Unit tests for continuous_write.py to improve coverage."""

from __future__ import annotations

import gc
import os
from unittest.mock import Mock, patch

import pytest

import chat_downloader.debugging as _debugging
from chat_downloader.output.continuous_write import (
    ContinuousFileWriter,
    ContinuousWriter,
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)


class _DummyWriter(ContinuousFileWriter):
    """Minimal concrete ContinuousFileWriter subclass used for testing."""

    def write(self, item: object, flush: bool = False) -> None:
        pass


def test_base_writer_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContinuousFileWriter("unused.txt")  # type: ignore[abstract]


@pytest.mark.parametrize("operation", ["close", "flush"])
def test_file_errors_are_propagated(tmp_path, operation):
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    writer.file = Mock()
    getattr(writer.file, operation).side_effect = OSError("disk full")
    with patch.object(os, "fsync") as fsync, pytest.raises(OSError, match="disk full"):
        if operation == "close":
            writer.close()
        else:
            writer._persist_after_write()
    if operation == "flush":
        fsync.assert_not_called()  # flush error returns before fsync


@pytest.mark.parametrize(
    ("writer_class", "items", "expected"),
    [
        (JsonLinesContinuousWriter, [{"key": "value"}], '{"key": "value"}\n'),
        (
            JsonLinesContinuousWriter,
            [{"a": 1}, {"b": 2}, {"c": 3}],
            '{"a": 1}\n{"b": 2}\n{"c": 3}\n',
        ),
        (TextContinuousWriter, ["Hello, world!"], "Hello, world!\n"),
    ],
)
@pytest.mark.parametrize("flush", [False, True])
def test_writes_complete_records(tmp_path, writer_class, items, expected, flush):
    path = tmp_path / "test.txt"
    writer = writer_class(str(path))
    try:
        for item in items:
            writer.write(item, flush=flush)
        # Data is visible before close, including when explicit flush is disabled.
        assert path.read_text(encoding="utf-8") == expected
    finally:
        writer.close()
    with pytest.raises(RuntimeError, match="initialized"):
        writer.write(items[0])


@pytest.mark.parametrize(
    ("writer_class", "overwrite", "initial", "item", "expected"),
    [
        (TextContinuousWriter, True, "stale\n", "fresh", "fresh\n"),
        (ContinuousWriter, False, "kept", None, "kept\n"),
    ],
)
def test_existing_file_policy(
    tmp_path, writer_class, overwrite, initial, item, expected
):
    path = tmp_path / "test.txt"
    path.write_text(initial, encoding="utf-8")
    writer = writer_class(str(path), overwrite=overwrite)
    if item is not None:
        writer.write(item)
    writer.close()
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("filename", "options", "message"),
    [
        *[
            (
                f"test.{ext}" if ext else "test",
                {"overwrite": True},
                r"Use a \.jsonl or \.txt output path",
            )
            for ext in ["csv", "json", "xyz", ""]
        ],
        ("test.txt", {"format": "json", "lazy_initialise": True}, "Use a"),
        ("test.jsonl", {"format": "csv", "lazy_initialise": True}, "Use a"),
        ("test.jsonl", {"format": "txt", "lazy_initialise": True}, "does not match"),
    ],
)
def test_factory_rejects_invalid_output_without_creating_file(
    tmp_path, filename, options, message
):
    path = tmp_path / filename
    with pytest.raises(ValueError, match=message):
        ContinuousWriter(str(path), **options)
    assert not path.exists()


@pytest.mark.parametrize(
    ("extension", "item", "expected", "output_mode"),
    [
        ("jsonl", {"key": "value"}, '{"key": "value"}\n', "raw"),
        ("txt", "text", "text\n", "formatted"),
    ],
)
@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("options", [{"sort_keys": True}, {}])
def test_factory_selection_and_initialization(
    tmp_path, extension, item, expected, output_mode, lazy, options
) -> None:
    path = tmp_path / "nested" / "deep" / f"test.{extension}"
    with ContinuousWriter(str(path), lazy_initialise=lazy, **options) as writer:
        assert writer.is_initialised() is (not lazy)
        assert path.exists() is (not lazy)
        writer.write(item)
        writer.initialize()
        writer.initialize()  # Repeated initialization must not truncate output.
        assert writer.is_initialised()
        assert writer.output_mode == output_mode
        assert writer.is_default() is (output_mode == "formatted")
        assert writer.sort_keys is options.get("sort_keys")
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("open failed")])
def test_initialize_failure_leaves_writer_uninitialized(tmp_path, error) -> None:
    writer = ContinuousWriter(str(tmp_path / "test.jsonl"), lazy_initialise=True)
    writer._open_writer = Mock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        writer.initialize()
    assert writer.writer is None


@pytest.mark.parametrize("operation", ["initialize", "write"])
def test_factory_lazy_init_recovers_after_validation_failure(tmp_path, operation):
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        if operation == "write":
            writer.write({"first": 1})
        else:
            writer._initialize_if_needed()
    assert not writer.is_initialised()
    path = tmp_path / "test.jsonl"
    writer.file_name = str(path)
    writer.write({"second": 2})
    writer.close()
    assert writer.is_initialised()
    assert '"second": 2' in path.read_text(encoding="utf-8")


def test_factory_unknown_kwargs_not_accessible_as_attributes(tmp_path) -> None:
    writer = ContinuousWriter(
        str(tmp_path / "test.jsonl"), lazy_initialise=True, custom_option="value"
    )
    with pytest.raises(AttributeError):
        _ = writer.custom_option


@pytest.mark.parametrize("error_type", [OSError, RuntimeError, ReferenceError])
def test_factory_del_io_error_log_contained_in_test(tmp_path, error_type) -> None:
    # Collect before leaving the patch: delayed GC must not leak logs to later tests.
    writer = ContinuousWriter(str(tmp_path / "test.jsonl"), lazy_initialise=True)
    writer.close = Mock(side_effect=error_type("disk full"))

    with patch.object(_debugging.logger, "debug") as mock_debug:
        writer.__del__()  # must not raise
        del writer
        gc.collect()

    logged = [call.args[0] for call in mock_debug.call_args_list]
    assert any("Suppressed error" in msg and "disk full" in msg for msg in logged)


def test_continuous_file_writer_closed_file_branch() -> None:
    from types import SimpleNamespace

    writer = _DummyWriter("unused.txt")
    writer.file = SimpleNamespace(closed=True)
    writer.close()
    assert writer.file is None


def test_factory_requires_initialization_to_produce_writer(tmp_path) -> None:

    class MissingWriter(ContinuousWriter):
        def _initialize_if_needed(self) -> None:
            return None

    missing = MissingWriter(str(tmp_path / "missing.txt"), lazy_initialise=True)
    with pytest.raises(RuntimeError, match="Writer was not initialized"):
        missing.write("x")

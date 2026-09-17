# SPDX-License-Identifier: MIT

"""Unit tests for continuous_write.py to improve coverage."""

from __future__ import annotations

import gc
import os
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import pytest

import chat_downloader.debugging as _debugging
from chat_downloader.output.continuous_write import (
    ContinuousFileWriter,
    ContinuousWriter,
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)

if TYPE_CHECKING:
    import pathlib


class _DummyWriter(ContinuousFileWriter):
    """Minimal concrete ContinuousFileWriter subclass used for testing."""

    def write(self, item: object, flush: bool = False) -> None:
        pass


def test_base_writer_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContinuousFileWriter("unused.txt")  # type: ignore[abstract]


def test_close_oserror_is_logged_and_reraised(tmp_path: pathlib.Path) -> None:
    path = str(tmp_path / "test.txt")
    writer = _DummyWriter(path)
    mock_file = Mock()
    mock_file.close.side_effect = OSError("disk full")
    writer.file = mock_file
    writer.file_name = path
    with pytest.raises(OSError):
        writer.close()


def test_persist_after_write_flush_oserror_is_propagated(
    tmp_path: pathlib.Path,
) -> None:
    path = str(tmp_path / "test.txt")
    writer = _DummyWriter(path)
    mock_file = Mock()
    mock_file.flush.side_effect = OSError("disk full")
    writer.file = mock_file
    writer.file_name = path
    with (
        patch.object(os, "fsync") as mock_fsync,
        pytest.raises(OSError, match="disk full"),
    ):
        writer._persist_after_write()
    mock_fsync.assert_not_called()  # returns before reaching fsync


# --- JsonLinesContinuousWriter ---


@pytest.mark.parametrize(
    ("writer_class", "extension", "items", "expected"),
    [
        (JsonLinesContinuousWriter, "jsonl", [{"key": "value"}], '{"key": "value"}\n'),
        (
            JsonLinesContinuousWriter,
            "jsonl",
            [{"a": 1}, {"b": 2}, {"c": 3}],
            '{"a": 1}\n{"b": 2}\n{"c": 3}\n',
        ),
        (TextContinuousWriter, "txt", ["Hello, world!"], "Hello, world!\n"),
    ],
)
@pytest.mark.parametrize("flush", [False, True])
def test_writes_complete_records(
    tmp_path, writer_class, extension, items, expected, flush
):
    path = tmp_path / f"test.{extension}"
    writer = writer_class(str(path))
    try:
        for item in items:
            writer.write(item, flush=flush)
        # Data is visible before close, including when explicit flush is disabled.
        assert path.read_text(encoding="utf-8") == expected
    finally:
        writer.close()


def test_txt_overwrite_true_truncates_existing_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "test.txt"
    path.write_text("stale\n", encoding="utf-8")
    writer = TextContinuousWriter(str(path), overwrite=True)
    writer.write("fresh")
    writer.close()
    assert path.read_text(encoding="utf-8") == "fresh\n"


# --- ContinuousWriter factory ---


@pytest.mark.parametrize("extension", ["csv", "json", "xyz", ""])
def test_factory_rejects_unsupported_extension_without_creating_file(
    tmp_path: pathlib.Path, extension: str
) -> None:
    path = str(tmp_path / (f"test.{extension}" if extension else "test"))
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(path, overwrite=True)
    assert not os.path.exists(path)


@pytest.mark.parametrize(
    ("extension", "item", "expected", "output_mode"),
    [
        ("jsonl", {"key": "value"}, '{"key": "value"}\n', "raw"),
        ("txt", "text", "text\n", "formatted"),
    ],
)
@pytest.mark.parametrize("lazy", [False, True])
def test_factory_selection_and_initialization(
    tmp_path, extension, item, expected, output_mode, lazy
) -> None:
    path = tmp_path / f"test.{extension}"
    with ContinuousWriter(str(path), lazy_initialise=lazy) as writer:
        assert writer.is_initialised() is (not lazy)
        assert path.exists() is (not lazy)
        writer.write(item)
        writer.initialize()
        writer.initialize()  # Repeated initialization must not truncate output.
        assert writer.is_initialised()
        assert writer.output_mode == output_mode
        assert writer.is_default() is (output_mode == "formatted")
    assert path.read_text(encoding="utf-8") == expected


def test_factory_validate_file_name_raises() -> None:
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        writer._initialize_if_needed()
    assert not writer.is_initialised()


@pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("open failed")])
def test_initialize_failure_leaves_writer_uninitialized(tmp_path, error) -> None:
    writer = ContinuousWriter(str(tmp_path / "test.jsonl"), lazy_initialise=True)
    writer._open_writer = Mock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        writer.initialize()
    assert writer.writer is None


def test_factory_lazy_init_can_recover_after_validation_failure(
    tmp_path: pathlib.Path,
) -> None:
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        writer.write({"first": 1})
    assert not writer.is_initialised()

    path = str(tmp_path / "test.jsonl")
    writer.file_name = path
    writer.write({"second": 2})
    writer.close()

    assert writer.is_initialised()
    with open(path, encoding="utf-8") as fh:
        assert '"second": 2' in fh.read()


@pytest.mark.parametrize(
    ("extension", "format_name", "message"),
    [
        ("txt", "json", "Use a"),
        ("jsonl", "csv", "Use a"),
        ("jsonl", "txt", "does not match"),
    ],
)
def test_factory_rejects_invalid_explicit_format(
    tmp_path, extension, format_name, message
) -> None:
    path = tmp_path / f"test.{extension}"
    with pytest.raises(ValueError, match=message):
        ContinuousWriter(str(path), format=format_name, lazy_initialise=True)
    assert not path.exists()


def test_factory_unknown_kwargs_not_accessible_as_attributes(
    tmp_path: pathlib.Path,
) -> None:
    writer = ContinuousWriter(
        str(tmp_path / "test.jsonl"), lazy_initialise=True, custom_option="value"
    )
    with pytest.raises(AttributeError):
        _ = writer.custom_option


def test_factory_parent_directory_created(tmp_path: pathlib.Path) -> None:
    nested_path = str(tmp_path / "nested" / "deep" / "test.jsonl")
    with ContinuousWriter(nested_path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert os.path.exists(nested_path)


@pytest.mark.parametrize("error_type", [OSError, RuntimeError, ReferenceError])
def test_factory_del_io_error_log_contained_in_test(
    tmp_path: pathlib.Path,
    error_type: type[Exception],
) -> None:
    """Regression: __del__ debug log for a suppressed OSError must not escape.

    In Python 3.14 the incremental GC can delay object finalization past the
    test boundary, causing the suppression log to fire while a later test has
    patched dbg.logger.debug, corrupting that test's mock call history.
    """
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


def test_continuous_writer_preserves_existing_file_without_overwrite(
    tmp_path,
) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("kept", encoding="utf-8")

    writer = ContinuousWriter(str(path), overwrite=False)
    writer.close()

    assert path.read_text(encoding="utf-8") == "kept\n"


@pytest.mark.parametrize(
    ("writer_class", "extension", "item"),
    [
        (JsonLinesContinuousWriter, "jsonl", {"a": 1}),
        (TextContinuousWriter, "txt", "hello"),
    ],
)
def test_closed_writer_rejects_writes(tmp_path, writer_class, extension, item) -> None:
    writer = writer_class(str(tmp_path / f"sample.{extension}"))
    writer.close()
    with pytest.raises(RuntimeError, match="initialized"):
        writer.write(item)


def test_factory_requires_initialization_to_produce_writer(tmp_path) -> None:

    class MissingWriter(ContinuousWriter):
        def _initialize_if_needed(self) -> None:
            return None

    missing = MissingWriter(str(tmp_path / "missing.txt"), lazy_initialise=True)
    with pytest.raises(RuntimeError, match="Writer was not initialized"):
        missing.write("x")

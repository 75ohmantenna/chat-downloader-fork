# SPDX-License-Identifier: MIT

"""Unit tests for continuous_write.py to improve coverage."""

from __future__ import annotations

import gc
import json
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

# --- path helpers ---


def _jsonl_path(tmp_path: pathlib.Path, name: str = "test.jsonl") -> str:
    return str(tmp_path / name)


def _txt_path(tmp_path: pathlib.Path, name: str = "test.txt") -> str:
    return str(tmp_path / name)


def _ext_path(tmp_path: pathlib.Path, ext: str) -> str:
    return str(tmp_path / f"test.{ext}")


# --- ContinuousFileWriter base ---


class _DummyWriter(ContinuousFileWriter):
    """Minimal concrete ContinuousFileWriter subclass used for testing."""

    def write(self, item: object, flush: bool = False) -> None:
        pass


def test_base_writer_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContinuousFileWriter("unused.txt")  # type: ignore[abstract]


def test_flush_with_no_file(tmp_path: pathlib.Path) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    writer.flush()  # should not raise when file is None


def test_flush_with_file(tmp_path: pathlib.Path) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    mock_file = Mock()
    writer.file = mock_file

    writer.flush()

    mock_file.flush.assert_called_once()


def test_close_with_no_file(tmp_path: pathlib.Path) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    writer.close()  # should not raise


def test_close_oserror_is_logged_and_reraised(tmp_path: pathlib.Path) -> None:
    path = str(tmp_path / "test.txt")
    writer = _DummyWriter(path)
    mock_file = Mock()
    mock_file.close.side_effect = OSError("disk full")
    writer.file = mock_file
    writer.file_name = path
    with pytest.raises(OSError):
        writer.close()


def test_persist_after_write_no_file_is_noop(tmp_path: pathlib.Path) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    writer._persist_after_write()  # file is None; must be a no-op


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


def test_persist_after_write_skips_non_file_descriptor(
    tmp_path: pathlib.Path,
) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    mock_file = Mock()
    mock_file.fileno.side_effect = ValueError("no descriptor")
    writer.file = mock_file
    writer._last_fsync_monotonic = 0

    with (
        patch(
            "chat_downloader.output.writers.time.monotonic",
            return_value=1000,
        ),
        patch("chat_downloader.output.writers.log") as mock_log,
    ):
        writer._persist_after_write()

    mock_log.assert_called_once_with(
        "debug",
        f"fsync() skipped on {writer.file_name}: no descriptor",
    )


# --- JsonLinesContinuousWriter ---


def test_jsonl_write_single_item(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    writer = JsonLinesContinuousWriter(path)
    writer.write({"key": "value"})
    writer.close()
    with open(path) as f:
        assert json.loads(f.readline()) == {"key": "value"}


def test_jsonl_write_multiple_items(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    writer = JsonLinesContinuousWriter(path)
    writer.write({"a": 1})
    writer.write({"b": 2})
    writer.write({"c": 3})
    writer.close()
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[2]) == {"c": 3}


def test_jsonl_write_with_flush(tmp_path: pathlib.Path) -> None:
    path = _jsonl_path(tmp_path)
    writer = JsonLinesContinuousWriter(path)
    writer.write({"key": "value"}, flush=True)
    writer.close()


# --- TextContinuousWriter ---


def test_txt_write_string(tmp_path: pathlib.Path) -> None:
    path = _txt_path(tmp_path)
    writer = TextContinuousWriter(path)
    writer.write("Hello, world!")
    writer.close()
    with open(path) as f:
        assert "Hello, world!" in f.read()


def test_txt_write_with_flush(tmp_path: pathlib.Path) -> None:
    path = _txt_path(tmp_path)
    writer = TextContinuousWriter(path)
    writer.write("line1", flush=True)
    writer.close()


def test_txt_overwrite_true_truncates_existing_file(
    tmp_path: pathlib.Path,
) -> None:
    path = _txt_path(tmp_path)
    with open(path, "w", encoding="utf-8") as f:
        f.write("stale\n")
    writer = TextContinuousWriter(path, overwrite=True)
    writer.write("fresh")
    writer.close()
    with open(path, encoding="utf-8") as f:
        assert f.read().splitlines() == ["fresh"]


# --- ContinuousWriter factory ---


def test_factory_json_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(path, overwrite=True)
    assert not os.path.exists(path)


def test_factory_jsonl_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert isinstance(writer.writer, JsonLinesContinuousWriter)


@pytest.mark.parametrize("extension", ["csv", "json", "xyz", ""])
def test_factory_rejects_unsupported_extension_without_creating_file(
    tmp_path: pathlib.Path, extension: str
) -> None:
    path = str(tmp_path / (f"test.{extension}" if extension else "test"))
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(path, overwrite=True)
    assert not os.path.exists(path)


def test_factory_txt_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write("text")
    assert isinstance(writer.writer, TextContinuousWriter)


def test_factory_format_json_rejected(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(path, overwrite=True, format="json")


def test_factory_lazy_initialise_true(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    assert not writer.is_initialised()
    assert not os.path.exists(path)
    writer.write({"key": "value"})
    assert writer.is_initialised()
    assert os.path.exists(path)
    writer.close()


def test_factory_lazy_initialise_false(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=False)
    assert os.path.exists(path)
    assert writer.is_initialised()
    writer.close()


def test_factory_initialize_if_needed_idempotent(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    writer._initialize_if_needed()
    writer._initialize_if_needed()
    assert writer.is_initialised()
    writer.close()


def test_factory_initialize_public_alias(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    writer.initialize()
    assert writer.is_initialised()
    writer.close()


def test_factory_validate_file_name_raises() -> None:
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        writer._initialize_if_needed()
    assert not writer.is_initialised()


def test_initialize_if_needed_clears_writer_on_oserror(
    tmp_path: pathlib.Path,
) -> None:
    writer = ContinuousWriter(_ext_path(tmp_path, "jsonl"), lazy_initialise=True)
    writer._open_writer = lambda _: (_ for _ in ()).throw(  # type: ignore[method-assign]
        OSError("disk full")
    )
    with pytest.raises(OSError, match="disk full"):
        writer._initialize_if_needed()
    assert writer._writer is None


def test_factory_lazy_init_can_recover_after_validation_failure(
    tmp_path: pathlib.Path,
) -> None:
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        writer.write({"first": 1})
    assert not writer.is_initialised()

    path = _ext_path(tmp_path, "jsonl")
    writer.file_name = path
    writer.write({"second": 2})
    writer.close()

    assert writer.is_initialised()
    with open(path, encoding="utf-8") as fh:
        assert '"second": 2' in fh.read()


def test_factory_file_name_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    writer = ContinuousWriter(path, lazy_initialise=True)
    assert writer.file_name == path
    new_path = _ext_path(tmp_path, "jsonl")
    writer.file_name = new_path
    assert writer.file_name == new_path


def test_factory_overwrite_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    assert ContinuousWriter(path, overwrite=True, lazy_initialise=True).overwrite
    assert not ContinuousWriter(path, overwrite=False, lazy_initialise=True).overwrite


def test_factory_rejects_unsupported_explicit_format(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
        ContinuousWriter(path, format="csv", lazy_initialise=True)


def test_factory_rejects_format_extension_mismatch(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    with pytest.raises(ValueError, match="does not match"):
        ContinuousWriter(path, format="txt", lazy_initialise=True)


def test_factory_lazy_initialise_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    assert ContinuousWriter(path, lazy_initialise=True).lazy_initialise


def test_factory_explicit_writer_option_properties(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, lazy_initialise=True, sort_keys=True)
    assert writer.sort_keys


def test_factory_unknown_kwargs_not_accessible_as_attributes(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, lazy_initialise=True, custom_option="value")
    assert writer._writer_kwargs["custom_option"] == "value"
    with pytest.raises(AttributeError):
        _ = writer.custom_option


def test_factory_unknown_attribute_raises(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, lazy_initialise=True)
    with pytest.raises(AttributeError):
        _ = writer.nonexistent_attribute


def test_factory_is_default_true_for_txt(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    writer = ContinuousWriter(path, overwrite=True)
    assert writer.is_default()
    assert writer.output_mode == "formatted"
    writer.close()


def test_factory_is_default_false_for_jsonl(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True)
    assert not writer.is_default()
    assert writer.output_mode == "raw"
    writer.close()


def test_factory_context_manager_enter(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    assert writer.__enter__() is writer
    writer.close()


def test_factory_context_manager_exit(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write({"key": "value"})


def test_factory_write_triggers_lazy_init(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    assert not writer.is_initialised()
    writer.write({"a": 1})
    assert writer.is_initialised()
    writer.close()


def test_factory_parent_directory_created(tmp_path: pathlib.Path) -> None:
    nested_path = str(tmp_path / "nested" / "deep" / "test.jsonl")
    with ContinuousWriter(nested_path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert os.path.exists(nested_path)


def test_factory_del_suppresses_io_cleanup_errors(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True)
    writer.write({"key": "value"})
    writer.close = Mock(side_effect=OSError("cleanup error"))
    writer.__del__()  # should not raise
    del writer
    gc.collect()


def test_factory_del_io_error_log_contained_in_test(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: __del__ debug log for a suppressed OSError must not escape.

    In Python 3.14 the incremental GC can delay object finalisation past the
    test boundary, causing the suppression log to fire while a later test has
    patched dbg.logger.debug, corrupting that test's mock call history.
    """
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True)
    writer.write({"key": "value"})
    writer.close = Mock(side_effect=OSError("disk full"))

    with patch.object(_debugging.logger, "debug") as mock_debug:
        writer.__del__()  # must not raise
        del writer
        gc.collect()

    logged = [call.args[0] for call in mock_debug.call_args_list]
    assert any("Suppressed error" in msg and "disk full" in msg for msg in logged)


def test_factory_del_suppresses_non_io_cleanup_errors(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "jsonl")
    writer = ContinuousWriter(path, overwrite=True)
    writer.write({"key": "value"})
    writer.close = Mock(side_effect=RuntimeError("cleanup error"))
    writer.__del__()  # should not raise during interpreter shutdown
    writer.close = Mock()


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

    assert path.read_text(encoding="utf-8") == "kept"


def test_jsonl_text_and_continuous_writer_edge_paths(tmp_path) -> None:
    jsonl = JsonLinesContinuousWriter(str(tmp_path / "sample.jsonl"))
    jsonl.file = None
    with pytest.raises(RuntimeError, match="initialized"):
        jsonl.write({"a": 1})

    txt = TextContinuousWriter(str(tmp_path / "sample.txt"))
    txt.file = None
    with pytest.raises(RuntimeError, match="initialized"):
        txt.write("hello")

    lazy = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError, match="File name not set"):
        lazy.initialize()
    broken = ContinuousWriter(str(tmp_path / "broken.jsonl"), lazy_initialise=True)
    broken._open_writer = lambda _file_name: (_ for _ in ()).throw(
        RuntimeError("open failed")
    )  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="open failed"):
        broken.initialize()
    assert broken.writer is None

    class MissingWriter(ContinuousWriter):
        def _initialize_if_needed(self) -> None:
            return None

    missing = MissingWriter(str(tmp_path / "missing.txt"), lazy_initialise=True)
    with pytest.raises(RuntimeError, match="Writer was not initialized"):
        missing.write("x")


def test_continuous_writer_del_suppresses_ignored_errors(monkeypatch, tmp_path) -> None:
    messages: list[str] = []
    writer = ContinuousWriter(str(tmp_path / "del.txt"), lazy_initialise=True)
    writer.close = lambda: (_ for _ in ()).throw(ReferenceError("gone"))  # type: ignore[method-assign]
    monkeypatch.setattr(
        "chat_downloader.output.continuous_write.log",
        lambda _level, message: messages.append(message),
    )

    writer.__del__()

    assert any(
        "Suppressed error during garbage-collection close" in m for m in messages
    )

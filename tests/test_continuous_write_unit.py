# SPDX-License-Identifier: MIT

"""Unit tests for continuous_write.py to improve coverage."""

import csv
import json
import os
import pathlib
import shutil
from unittest.mock import Mock, patch

import pytest

from chat_downloader.output.continuous_write import (
    ContinuousFileWriter,
    ContinuousWriter,
    CsvContinuousWriter,
    JsonContinuousWriter,
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)

# --- path helpers ---


def _json_path(tmp_path: pathlib.Path, name: str = "test.json") -> str:
    """Pre-create an empty file; JsonContinuousWriter requires rb+ mode."""
    path = tmp_path / name
    path.touch()
    return str(path)


def _csv_path(tmp_path: pathlib.Path, name: str = "test.csv") -> str:
    return str(tmp_path / name)


def _jsonl_path(tmp_path: pathlib.Path, name: str = "test.jsonl") -> str:
    return str(tmp_path / name)


def _txt_path(tmp_path: pathlib.Path, name: str = "test.txt") -> str:
    return str(tmp_path / name)


def _ext_path(tmp_path: pathlib.Path, ext: str) -> str:
    return str(tmp_path / f"test.{ext}")


# --- ContinuousFileWriter base ---


class _DummyWriter(ContinuousFileWriter):
    """Minimal concrete subclass for testing ContinuousFileWriter base behavior."""

    def write(self, item: object, flush: bool = False) -> None:
        pass


def test_base_writer_is_abstract() -> None:
    with pytest.raises(TypeError):
        ContinuousFileWriter("unused.txt")  # type: ignore[abstract]


def test_flush_with_no_file(tmp_path: pathlib.Path) -> None:
    writer = _DummyWriter(str(tmp_path / "test.txt"))
    writer.flush()  # should not raise when file is None


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


# --- JsonContinuousWriter ---


def test_json_write_single_item_no_indent(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path)
    writer.write({"key": "value"})
    writer.close()
    with open(path) as f:
        assert json.load(f) == [{"key": "value"}]


def test_json_write_single_item_with_indent(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, indent=2)
    writer.write({"key": "value"})
    writer.close()
    with open(path) as f:
        content = f.read()
    assert "\n" in content
    assert json.loads(content) == [{"key": "value"}]


def test_json_write_multiple_items(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path)
    writer.write({"a": 1})
    writer.write({"b": 2})
    writer.write({"c": 3})
    writer.close()
    with open(path) as f:
        data = json.load(f)
    assert len(data) == 3
    assert data[0] == {"a": 1}
    assert data[2] == {"c": 3}


def test_json_write_multiple_items_with_indent(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, indent=2)
    writer.write({"a": 1})
    writer.write({"b": 2})
    writer.close()
    with open(path) as f:
        assert len(json.load(f)) == 2


def test_json_is_first_flag(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path)
    assert writer._is_first
    writer.write({"x": 1})
    assert not writer._is_first
    writer.close()


def test_json_write_with_flush(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path)
    writer.write({"key": "value"}, flush=True)
    writer.close()
    with open(path) as f:
        assert json.load(f) == [{"key": "value"}]


def test_json_flush_with_file_open(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path)
    writer.write({"a": 1})
    writer.flush()  # should not raise
    writer.close()


def test_json_overwrite_false_loads_previous(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    w1 = JsonContinuousWriter(path, overwrite=True)
    w1.write({"first": True})
    w1.close()

    w2 = JsonContinuousWriter(path, overwrite=False)
    w2.write({"second": True})
    w2.close()

    with open(path) as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0] == {"first": True}
    assert data[1] == {"second": True}


def test_json_recover_from_corrupted_json(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    with open(path, "w") as f:
        f.write("not valid json at all {{{")

    writer = JsonContinuousWriter(path, overwrite=False)
    writer.write({"recovered": True})
    writer.close()

    backup_files = [p for p in tmp_path.iterdir() if ".corrupted." in p.name]
    assert len(backup_files) > 0

    with open(path) as f:
        assert json.load(f) == [{"recovered": True}]


def test_json_recover_from_corrupted_json_backup_oserror(
    tmp_path: pathlib.Path,
) -> None:
    path = _json_path(tmp_path)
    with open(path, "w") as f:
        f.write("not valid json {{{")

    with patch.object(shutil, "copyfileobj", side_effect=OSError("no space")):
        writer = JsonContinuousWriter(path, overwrite=False)
        writer.write({"recovered": True})
        writer.close()


def test_json_append_to_empty_array_writes_valid_separator(
    tmp_path: pathlib.Path,
) -> None:
    path = _json_path(tmp_path)
    with open(path, "w", encoding="utf-8") as f:
        f.write("[]")

    writer = JsonContinuousWriter(path, overwrite=False)
    writer.write({"first": 1})
    writer.write({"second": 2})
    writer.close()

    with open(path, encoding="utf-8") as f:
        assert json.load(f) == [{"first": 1}, {"second": 2}]


@pytest.mark.parametrize(
    "indent,expected",
    [(4, "    "), (None, "")],
)
def test_json_calculate_padding(
    tmp_path: pathlib.Path, indent: int | None, expected: str
) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, indent=indent)
    assert writer._calculate_padding() == expected
    writer.close()


def test_json_multiline_indent(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, indent=2)
    result = writer._multiline_indent("line1\nline2\nline3")
    assert all(line.startswith("  ") for line in result.split("\n") if line)
    writer.close()


@pytest.mark.parametrize(
    "indent,expected",
    [(2, "\n"), (None, "")],
)
def test_json_newline_padding(
    tmp_path: pathlib.Path, indent: int | None, expected: str
) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, indent=indent)
    assert writer._newline_padding == expected
    writer.close()


def test_json_sort_keys(tmp_path: pathlib.Path) -> None:
    path = _json_path(tmp_path)
    writer = JsonContinuousWriter(path, sort_keys=True)
    writer.write({"z": 3, "a": 1, "m": 2})
    writer.close()
    with open(path) as f:
        content = f.read()
    assert content.index('"a"') < content.index('"z"')


# --- CsvContinuousWriter ---


def test_csv_write_single_item(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"name": "John", "age": "30"})
    writer.close()
    with open(path) as f:
        content = f.read()
    assert "name" in content
    assert "John" in content


def test_csv_write_multiple_items_same_columns(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"name": "Alice", "age": "25"})
    writer.write({"name": "Bob", "age": "30"})
    writer.close()
    with open(path) as f:
        assert len(f.readlines()) == 3  # header + 2 rows


def test_csv_write_with_new_columns(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"name": "Alice"})
    writer.write({"name": "Bob", "age": "30"})
    writer.close()
    with open(path) as f:
        content = f.read()
    assert "age" in content
    assert "name" in content


def test_csv_overwrite_false_loads_previous(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    w1 = CsvContinuousWriter(path, overwrite=True)
    w1.write({"col": "first"})
    w1.close()

    w2 = CsvContinuousWriter(path, overwrite=False)
    w2.write({"col": "second"})
    w2.close()

    with open(path) as f:
        assert len(f.readlines()) == 3  # header + 2 rows


def test_csv_write_with_flush(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"key": "value"}, flush=True)
    writer.close()


def test_csv_write_without_flatten(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"key": "value"}, flatten=False)
    writer.close()


def test_csv_has_new_columns(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path)
    writer.write({"a": 1})
    assert not writer._has_new_columns({"a": 2})
    assert writer._has_new_columns({"b": 1})
    writer.close()


def test_csv_sort_keys(tmp_path: pathlib.Path) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path, sort_keys=True)
    writer.write({"z": 3, "a": 1})
    writer.write({"z": 6, "a": 4, "m": 5})
    writer.close()
    with open(path) as f:
        cols = f.readline().strip().split(",")
    assert cols == sorted(cols)


def test_csv_new_columns_midstream_preserve_existing_rows(
    tmp_path: pathlib.Path,
) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path, sort_keys=False)
    writer.write({"name": "Alice"})
    writer.write({"name": "Bob", "age": "30"})
    writer.close()

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["name"] == "Alice"
    assert rows[0]["age"] == ""
    assert rows[1]["name"] == "Bob"
    assert rows[1]["age"] == "30"


def test_csv_overwrite_false_with_existing_headers_and_new_column(
    tmp_path: pathlib.Path,
) -> None:
    path = _csv_path(tmp_path)
    first = CsvContinuousWriter(path, overwrite=True, sort_keys=False)
    first.write({"name": "Alice", "city": "NYC"})
    first.close()

    second = CsvContinuousWriter(path, overwrite=False, sort_keys=False)
    second.write({"name": "Bob", "city": "LA", "age": "30"})
    second.close()

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        headers = list(rows[0].keys())
    assert headers == ["name", "city", "age"]
    assert len(rows) == 2
    assert rows[0]["age"] == ""
    assert rows[1]["age"] == "30"


def test_csv_sort_keys_stable_after_multiple_rewrites(
    tmp_path: pathlib.Path,
) -> None:
    path = _csv_path(tmp_path)
    writer = CsvContinuousWriter(path, sort_keys=True)
    writer.write({"z": "1"})
    writer.write({"z": "2", "a": "A"})
    writer.write({"z": "3", "a": "B", "m": "M"})
    writer.close()

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames is not None
        assert reader.fieldnames == sorted(reader.fieldnames)


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
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert isinstance(writer.writer, JsonContinuousWriter)


def test_factory_jsonl_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "jsonl")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert isinstance(writer.writer, JsonLinesContinuousWriter)


def test_factory_csv_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "csv")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert isinstance(writer.writer, CsvContinuousWriter)


def test_factory_txt_extension(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write("text")
    assert isinstance(writer.writer, TextContinuousWriter)


def test_factory_unknown_extension_uses_text(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "xyz")
    with ContinuousWriter(path, overwrite=True) as writer:
        writer.write("text")
    assert isinstance(writer.writer, TextContinuousWriter)


def test_factory_format_override(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    with ContinuousWriter(path, overwrite=True, format="json") as writer:
        writer.write({"key": "value"})
    assert isinstance(writer.writer, JsonContinuousWriter)


def test_factory_lazy_initialise_true(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    assert not writer.is_initialised()
    assert not os.path.exists(path)
    writer.write({"key": "value"})
    assert writer.is_initialised()
    assert os.path.exists(path)
    writer.close()


def test_factory_lazy_initialise_false(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=False)
    assert os.path.exists(path)
    assert writer.is_initialised()
    writer.close()


def test_factory_initialize_if_needed_idempotent(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    writer._initialize_if_needed()
    writer._initialize_if_needed()
    assert writer.is_initialised()
    writer.close()


def test_factory_initialize_public_alias(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    writer.initialize()
    assert writer.is_initialised()
    writer.close()


def test_factory_validate_file_name_raises() -> None:
    writer = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError):
        writer._initialize_if_needed()
    assert not writer.is_initialised()


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
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, lazy_initialise=True)
    assert writer.file_name == path
    new_path = _ext_path(tmp_path, "jsonl")
    writer.file_name = new_path
    assert writer.file_name == new_path


def test_factory_overwrite_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    assert ContinuousWriter(
        path, overwrite=True, lazy_initialise=True
    ).overwrite
    assert not ContinuousWriter(
        path, overwrite=False, lazy_initialise=True
    ).overwrite


def test_factory_format_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    assert (
        ContinuousWriter(path, format="csv", lazy_initialise=True).format
        == "csv"
    )


def test_factory_lazy_initialise_property(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    assert ContinuousWriter(path, lazy_initialise=True).lazy_initialise


def test_factory_explicit_writer_option_properties(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(
        path, lazy_initialise=True, indent=4, sort_keys=True
    )
    assert writer.indent == 4
    assert writer.sort_keys


def test_factory_unknown_kwargs_not_accessible_as_attributes(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, lazy_initialise=True, custom_option="value")
    assert writer._writer_kwargs["custom_option"] == "value"
    with pytest.raises(AttributeError):
        _ = writer.custom_option


def test_factory_unknown_attribute_raises(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, lazy_initialise=True)
    with pytest.raises(AttributeError):
        _ = writer.nonexistent_attribute


def test_factory_is_default_true_for_txt(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "txt")
    writer = ContinuousWriter(path, overwrite=True)
    assert writer.is_default()
    assert writer.output_mode == "formatted"
    writer.close()


def test_factory_is_default_false_for_json(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True)
    assert not writer.is_default()
    assert writer.output_mode == "raw"
    writer.close()


def test_factory_context_manager_enter(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    assert writer.__enter__() is writer
    writer.close()


def test_factory_context_manager_exit(tmp_path: pathlib.Path) -> None:
    path = _ext_path(tmp_path, "json")
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
    nested_path = str(tmp_path / "nested" / "deep" / "test.json")
    with ContinuousWriter(nested_path, overwrite=True) as writer:
        writer.write({"key": "value"})
    assert os.path.exists(nested_path)


def test_factory_del_suppresses_io_cleanup_errors(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True)
    writer.write({"key": "value"})
    writer.close = Mock(side_effect=OSError("cleanup error"))
    writer.__del__()  # should not raise


def test_factory_del_suppresses_non_io_cleanup_errors(
    tmp_path: pathlib.Path,
) -> None:
    path = _ext_path(tmp_path, "json")
    writer = ContinuousWriter(path, overwrite=True)
    writer.write({"key": "value"})
    writer.close = Mock(side_effect=RuntimeError("cleanup error"))
    writer.__del__()  # should not raise during interpreter shutdown
    writer.close = Mock()


def test_json_writer_rethrows_configure_existing_array_failure(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("[]", encoding="utf-8")

    def boom(self):
        raise RuntimeError("configure failed")

    monkeypatch.setattr(
        JsonContinuousWriter, "_configure_existing_json_array", boom
    )

    with pytest.raises(RuntimeError, match="configure failed"):
        JsonContinuousWriter(str(path), overwrite=False)


def test_json_writer_runtime_guards_and_multiline_format(tmp_path) -> None:
    path = tmp_path / "sample.json"
    path.write_text("", encoding="utf-8")
    real = JsonContinuousWriter(str(path), indent="  ")
    formatted = real._format_item_as_json({"a": 1, "b": {"c": 2}})
    assert "\n" in formatted
    real.close()

    detached = JsonContinuousWriter(str(path))
    detached.file = None
    with pytest.raises(RuntimeError, match="initialized"):
        detached.write({"a": 1})


def test_json_writer_string_indent_is_not_double_prefixed(tmp_path) -> None:
    path = tmp_path / "tabs.json"
    writer = JsonContinuousWriter(str(path), indent="\t")

    writer.write({"a": 1})
    writer.close()

    content = path.read_text(encoding="utf-8")
    assert '\t"a"' in content
    assert '\t\t"a"' not in content
    assert json.loads(content) == [{"a": 1}]


def test_json_writer_configure_existing_array_requires_file(tmp_path) -> None:
    path = tmp_path / "sample.json"
    path.write_text("", encoding="utf-8")
    writer = JsonContinuousWriter(str(path))
    writer.file = None
    with pytest.raises(RuntimeError, match="initialized"):
        writer._configure_existing_json_array()


def test_csv_and_continuous_writer_runtime_guards(tmp_path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_writer = ContinuousWriter(
        str(csv_path), format="csv", lazy_initialise=False
    ).writer
    csv_writer.file = None
    with pytest.raises(RuntimeError, match="initialized"):
        csv_writer._load_existing_columns()
    with pytest.raises(RuntimeError, match="initialized"):
        csv_writer._reset_csv_writer()
    with pytest.raises(RuntimeError, match="initialized"):
        csv_writer._handle_new_columns({"a": 1})


def test_continuous_file_writer_closed_file_branch() -> None:
    from types import SimpleNamespace

    writer = _DummyWriter("unused.txt")
    writer.file = SimpleNamespace(closed=True)
    writer.close()
    assert writer.file is None


def test_json_continuous_writer_empty_existing_file_and_close_error(
    monkeypatch, tmp_path
) -> None:
    from types import SimpleNamespace

    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")

    writer = JsonContinuousWriter(str(path), overwrite=False)
    writer.close()
    assert path.read_text(encoding="utf-8") == "[]"

    failing = JsonContinuousWriter(str(tmp_path / "close-error.json"))
    failing.write({"x": 1})
    mock_file = SimpleNamespace(
        closed=False,
        write=lambda _text: (_ for _ in ()).throw(OSError("disk full")),
        close=lambda: None,
    )
    failing.file = mock_file
    with pytest.raises(OSError):
        failing.close()


def test_csv_jsonl_text_and_continuous_writer_edge_paths(tmp_path) -> None:
    csv_writer = ContinuousWriter(
        str(tmp_path / "sample.csv"), lazy_initialise=True
    )
    csv_writer.initialize()
    with pytest.raises(TypeError, match="dictionary item"):
        csv_writer.write("bad")

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
    broken = ContinuousWriter(
        str(tmp_path / "broken.json"), lazy_initialise=True
    )
    broken._file_name = str(tmp_path / "broken.json")
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


def test_continuous_writer_del_suppresses_ignored_errors(
    monkeypatch, tmp_path
) -> None:
    messages: list[str] = []
    writer = ContinuousWriter(str(tmp_path / "del.txt"), lazy_initialise=True)
    writer.close = lambda: (_ for _ in ()).throw(ReferenceError("gone"))  # type: ignore[method-assign]
    monkeypatch.setattr(
        "chat_downloader.output.continuous_write.log",
        lambda _level, message: messages.append(message),
    )

    writer.__del__()

    assert any(
        "Suppressed error during garbage-collection close" in m
        for m in messages
    )

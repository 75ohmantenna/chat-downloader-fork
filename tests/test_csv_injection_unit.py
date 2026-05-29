# SPDX-License-Identifier: MIT

"""CSV injection escape: leading formula chars are prefixed with apostrophe."""

import csv
from pathlib import Path

import pytest

from chat_downloader.output.continuous_write import CsvContinuousWriter
from chat_downloader.output.csv_rewrite import csv_safe_item, csv_safe_value


@pytest.mark.parametrize("ch", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_safe_value_prefixes_formula_leads(ch: str) -> None:
    assert csv_safe_value(f"{ch}cmd|'/c calc'!A1") == f"'{ch}cmd|'/c calc'!A1"


def test_csv_safe_value_passes_through_safe_strings() -> None:
    assert csv_safe_value("normal message") == "normal message"
    assert csv_safe_value("") == ""
    assert csv_safe_value("3 + 4") == "3 + 4"  # leading digit is fine


def test_csv_safe_value_passes_through_non_strings() -> None:
    assert csv_safe_value(42) == 42
    assert csv_safe_value(None) is None
    assert csv_safe_value(3.14) == 3.14


def test_csv_safe_value_leaves_leading_whitespace_formula_untouched() -> None:
    # Only value[0] is checked; a leading space disables formula evaluation in
    # spreadsheets, so the value is intentionally not prefixed. Locks in
    # current behavior.
    assert csv_safe_value(" =1+1") == " =1+1"


def test_csv_safe_item_copies_dict() -> None:
    original = {"msg": "=danger", "ok": "fine"}
    safe = csv_safe_item(original)
    assert safe == {"msg": "'=danger", "ok": "fine"}
    # Original is not mutated.
    assert original == {"msg": "=danger", "ok": "fine"}


def test_csv_writer_escapes_hostile_message_on_disk(tmp_path: Path) -> None:
    """A hostile chat message must NOT be written as a live formula."""
    path = tmp_path / "chat.csv"
    writer = CsvContinuousWriter(str(path))
    try:
        # Seed columns so first row goes through the fast path.
        writer.write({"author": "alice", "message": "hello"})
        writer.write(
            {
                "author": "evil",
                "message": "=cmd|'/c calc'!A1",
            }
        )
    finally:
        writer.close()

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[1]["message"] == "'=cmd|'/c calc'!A1"
    # Verify no row is a raw formula.
    for row in rows:
        for value in row.values():
            assert not (value and value[0] in ("=", "+", "-", "@")), row


def test_csv_writer_escapes_during_rewrite_with_new_columns(
    tmp_path: Path,
) -> None:
    """When a row introduces a new column, the rewrite path also escapes."""
    path = tmp_path / "chat.csv"
    writer = CsvContinuousWriter(str(path))
    try:
        writer.write({"a": "first"})
        writer.write({"a": "second", "b": "@SUM(1,2)"})
    finally:
        writer.close()

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[-1]["b"] == "'@SUM(1,2)"


def test_rewrite_csv_cleans_up_temp_file_on_write_failure(
    tmp_path: Path,
) -> None:
    """If the write to the temp file fails, no orphan temp file is left."""
    import glob
    import io

    from chat_downloader.output.csv_rewrite import rewrite_csv_with_new_columns

    before = set(glob.glob(str(tmp_path / "*")))

    broken_file = io.StringIO("a\nval\n")
    broken_file.seek = lambda *_: (_ for _ in ()).throw(OSError("seek failed"))  # type: ignore[method-assign]

    try:
        rewrite_csv_with_new_columns(
            current_file=broken_file,
            file_name=str(tmp_path / "chat.csv"),
            columns=["a", "b"],
            item={"a": "x", "b": "y"},
        )
    except OSError:
        pass

    after = set(glob.glob(str(tmp_path / "*")))
    assert after == before, f"orphan temp files left: {after - before}"

# SPDX-License-Identifier: MIT

"""CSV rewrite helpers for continuous writers."""

from __future__ import annotations

import contextlib
import csv
import os
import tempfile
from pathlib import Path
from typing import IO, Any

# Leading characters that spreadsheet apps (Excel, Sheets, LibreOffice)
# interpret as the start of a formula. Chat messages are untrusted text;
# unescaped, a hostile message can execute on open.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def csv_safe_value(value: Any) -> Any:
    """Quote string values that start with spreadsheet formula triggers.

    Values beginning with a formula-trigger character are prefixed with a
    single quote so spreadsheet apps treat them as text, not formulas.
    Non-string values are returned unchanged.
    """
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_LEAD:
        return "'" + value
    return value


def csv_safe_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``item`` with each cell value made CSV-safe."""
    return {key: csv_safe_value(value) for key, value in item.items()}


def rewrite_csv_with_new_columns(
    *,
    current_file: IO[str],
    file_name: str,
    columns: list[str],
    item: dict[str, Any],
) -> None:
    """Rewrite an existing CSV file using updated column headers.

    The new file includes:
    - Updated header row
    - All existing rows from ``current_file``
    - The current ``item`` row
    """
    new_file_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=Path(file_name).parent,
            delete=False,
        ) as new_file:
            new_file_path = new_file.name
            csv_dict_writer = csv.DictWriter(new_file, fieldnames=columns)
            csv_dict_writer.writeheader()

            current_file.seek(0)
            # Existing rows were already escaped when first written; copy as-is.
            csv_dict_writer.writerows(csv.DictReader(current_file))
            csv_dict_writer.writerow(csv_safe_item(item))
            new_file.flush()
            os.fsync(new_file.fileno())

        current_file.close()
        Path(new_file_path).replace(file_name)
        new_file_path = None
    finally:
        if not current_file.closed:
            current_file.close()
        if new_file_path is not None:
            with contextlib.suppress(OSError):
                Path(new_file_path).unlink()

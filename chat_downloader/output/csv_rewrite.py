# SPDX-License-Identifier: MIT

"""CSV rewrite helpers for continuous writers."""

import csv
import os
import shutil
import tempfile
from typing import IO, Any


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
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=os.path.dirname(file_name) or None,
        delete=False,
    ) as new_file:
        csv_dict_writer = csv.DictWriter(new_file, fieldnames=columns)
        csv_dict_writer.writeheader()

        current_file.seek(0)
        csv_dict_writer.writerows(csv.DictReader(current_file))
        csv_dict_writer.writerow(item)
        new_file_path = new_file.name

    current_file.close()
    shutil.move(new_file_path, file_name)

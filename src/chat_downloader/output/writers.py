# SPDX-License-Identifier: MIT

"""Concrete continuous file writer implementations."""

from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Any

from chat_downloader.debugging import log
from chat_downloader.output.csv_rewrite import (
    csv_safe_item,
    rewrite_csv_with_new_columns,
)
from chat_downloader.utils.json_utils import flatten_json

# File operation constants
FILE_EMPTY_POSITION = 0
EXTENSION_INDEX = 1
MODE_APPEND_TEXT = "a"
MODE_APPEND_PLUS_TEXT = "a+"
MODE_WRITE_TEXT = "w"

# Wall-clock seconds between fsync()s. Per-record flush handles process
# crashes; fsync also survives OS-level events like power loss.
_FSYNC_INTERVAL_SECONDS = 60.0


def _assert_utc_aware(item: Any) -> None:
    """Guard against naive datetimes ending up in serialized output.

    The codebase contract is "timestamps are UTC, either as integer
    microseconds or as tz-aware datetimes." This is a cheap top-level
    scan that fails loudly if a regression introduces a naive datetime,
    which would otherwise be silently misinterpreted by downstream
    consumers as local-time.
    """
    if not isinstance(item, dict):
        return
    for key, value in item.items():
        if isinstance(value, _dt.datetime) and value.tzinfo is None:
            msg = (
                f"Output field {key!r} contains a naive datetime "
                f"({value!r}); chat_downloader emits UTC-aware datetimes "
                "only. Convert with .replace(tzinfo=datetime.UTC) before "
                "writing."
            )
            raise ValueError(msg)


class ContinuousFileWriter(ABC):
    """Abstract base for continuous file writers.

    Subclasses must implement write(). Provides shared close() and flush().
    """

    def __init__(
        self,
        file_name: str,
        *,
        overwrite: bool = True,
        **kwargs: Any,  # noqa: ARG002 — base class contract; subclasses consume kwargs
    ) -> None:
        """Initialise the writer for the given file path."""
        self.file_name = file_name
        self.overwrite = overwrite
        self.file: IO[Any] | None = None
        self._last_fsync_monotonic = time.monotonic()

    def close(self) -> None:
        """Close the underlying file handle if open."""
        if self.file:
            if getattr(self.file, "closed", False) is True:
                self.file = None
                return
            try:
                self.file.close()
            except OSError as e:
                log("warning", f"Error closing file {self.file_name}: {e}")
                raise
            finally:
                self.file = None

    @abstractmethod
    def write(self, item: dict[str, Any] | str, *, flush: bool = False) -> None:
        """Write a chat item to the file."""

    def flush(self) -> None:
        """Flush the underlying file buffer."""
        if self.file:
            self.file.flush()

    def _persist_after_write(self) -> None:
        """Flush after every record; fsync at most once per interval.

        Per-message flush moves bytes from Python buffers into the OS,
        protecting against SIGKILL. fsync forces the OS to commit them
        to disk, protecting against power events. fsync per record is
        too expensive on long live streams, so it runs on a timer.
        """
        if self.file is None:
            return
        try:
            self.file.flush()
        except OSError as e:
            log(
                "warning",
                f"flush() failed on {self.file_name}: {e}",
            )
            return
        now = time.monotonic()
        if now - self._last_fsync_monotonic < _FSYNC_INTERVAL_SECONDS:
            return
        self._last_fsync_monotonic = now
        try:
            os.fsync(self.file.fileno())
        except (OSError, AttributeError, ValueError) as e:
            # AttributeError/ValueError: in-memory or non-fd files (tests).
            log(
                "debug",
                f"fsync() skipped on {self.file_name}: {e}",
            )


class CsvContinuousWriter(ContinuousFileWriter):
    """Continuously write dicts to CSV, rewriting when new columns appear."""

    def __init__(
        self,
        file_name: str,
        *,
        sort_keys: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize a CSV writer, loading existing columns when appending."""
        super().__init__(file_name, **kwargs)
        self.sort_keys = sort_keys
        self.file = Path(self.file_name).open(  # noqa: SIM115
            MODE_APPEND_PLUS_TEXT,
            newline="",
            encoding="utf-8",
        )
        try:
            self.columns: list[str] = []

            if not self.overwrite:
                self._load_existing_columns()

            self._reset_csv_writer()
        except (OSError, csv.Error, RuntimeError):
            self.file.close()
            raise

    def _load_existing_columns(self) -> None:
        """Load existing CSV columns without reading all rows into memory."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        self.file.seek(FILE_EMPTY_POSITION)
        csv_reader = csv.DictReader(self.file)
        self.columns = list(csv_reader.fieldnames or [])
        self.file.seek(FILE_EMPTY_POSITION, os.SEEK_END)

    def _reset_csv_writer(self) -> None:
        """Recreate CSV writer with current column configuration."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        if self.columns:
            self.csv_dict_writer = csv.DictWriter(self.file, fieldnames=self.columns)
        else:
            self.csv_dict_writer = csv.DictWriter(self.file, fieldnames=[])

    def write(
        self,
        item: dict[str, Any] | str,
        *,
        flush: bool = False,
        flatten: bool = True,
    ) -> None:
        """Write a dict to the CSV file, flattening nested dicts by default."""
        if not isinstance(item, dict):
            msg = "CSV output requires a dictionary item"
            raise TypeError(msg)

        if flatten:
            item = flatten_json(item)

        if self._has_new_columns(item):
            self._handle_new_columns(item)
        else:
            self.csv_dict_writer.writerow(csv_safe_item(item))

        self._persist_after_write()
        if flush:
            self.flush()

    def _has_new_columns(self, item: dict[str, Any]) -> bool:
        """Return True if *item* contains columns not yet in the CSV."""
        return any(column not in self.columns for column in item)

    def _handle_new_columns(self, item: dict[str, Any]) -> None:
        """Rewrite the entire file to add newly discovered columns."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        new_columns = [column for column in item if column not in self.columns]
        self.columns.extend(new_columns)
        if self.sort_keys:
            self.columns.sort()

        old_file = self.file
        # rewrite_csv_with_new_columns closes old_file; prevent stale handle.
        self.file = None
        rewrite_csv_with_new_columns(
            current_file=old_file,
            file_name=self.file_name,
            columns=self.columns,
            item=item,
        )
        self.file = Path(self.file_name).open(  # noqa: SIM115
            MODE_APPEND_PLUS_TEXT,
            newline="",
            encoding="utf-8",
        )
        self._reset_csv_writer()


class JsonLinesContinuousWriter(ContinuousFileWriter):
    """Write one JSON object per line (JSONL), crash-safe for live streams."""

    def __init__(
        self,
        file_name: str,
        *,
        sort_keys: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize a JSON Lines writer."""
        super().__init__(file_name, **kwargs)
        self.sort_keys = sort_keys
        file_mode = MODE_WRITE_TEXT if self.overwrite else MODE_APPEND_TEXT
        self.file = Path(self.file_name).open(file_mode, encoding="utf-8")  # noqa: SIM115

    def write(self, item: Any, *, flush: bool = False) -> None:
        """Write *item* as a single JSON line."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        _assert_utc_aware(item)
        self.file.write(json.dumps(item, sort_keys=self.sort_keys) + "\n")
        self._persist_after_write()
        if flush:
            self.file.flush()


class TextContinuousWriter(ContinuousFileWriter):
    """Write items as plain text, one per line."""

    def __init__(self, file_name: str, **kwargs: Any) -> None:
        """Initialize a line-oriented text writer."""
        super().__init__(file_name, **kwargs)
        file_mode = MODE_WRITE_TEXT if self.overwrite else MODE_APPEND_TEXT
        self.file = Path(self.file_name).open(file_mode, encoding="utf-8")  # noqa: SIM115

    def write(self, item: Any, *, flush: bool = False) -> None:
        """Write *item* as a string line."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        self.file.write(str(item) + "\n")
        self._persist_after_write()
        if flush:
            self.file.flush()


_WRITER_CLASSES: dict[str, type[ContinuousFileWriter]] = {
    "csv": CsvContinuousWriter,
    "jsonl": JsonLinesContinuousWriter,
    "txt": TextContinuousWriter,
}

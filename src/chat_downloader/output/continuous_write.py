# SPDX-License-Identifier: MIT

"""Continuous file writers for streaming chat messages to disk."""

import csv
import datetime as _dt
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Any, Self

from chat_downloader.debugging import log
from chat_downloader.output.csv_rewrite import (
    csv_safe_item,
    rewrite_csv_with_new_columns,
)
from chat_downloader.utils.json_utils import flatten_json

# File operation constants
FILE_EMPTY_POSITION = 0
EXTENSION_INDEX = 1
DOT_PREFIX_LENGTH = 1

# File modes
MODE_APPEND_TEXT = "a"
MODE_APPEND_PLUS_TEXT = "a+"
MODE_WRITE_TEXT = "w"
_IGNORED_DEL_EXCEPTIONS = Exception
UNSUPPORTED_JSON_EXTENSION_MESSAGE = (
    "JSON array output is no longer supported. Use a .jsonl output path "
    "for structured chat output."
)

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
        self, file_name: str, overwrite: bool = True, **kwargs: Any
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
    def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
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
            raise RuntimeError("File must be initialized before use")
        self.file.seek(FILE_EMPTY_POSITION)
        csv_reader = csv.DictReader(self.file)
        self.columns = list(csv_reader.fieldnames or [])
        self.file.seek(FILE_EMPTY_POSITION, os.SEEK_END)

    def _reset_csv_writer(self) -> None:
        """Recreate CSV writer with current column configuration."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
        if self.columns:
            self.csv_dict_writer = csv.DictWriter(
                self.file, fieldnames=self.columns
            )
        else:
            self.csv_dict_writer = csv.DictWriter(self.file, fieldnames=[])

    def write(
        self,
        item: dict[str, Any] | str,
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
            raise RuntimeError("File must be initialized before use")
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
        sort_keys: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize a JSON Lines writer."""
        super().__init__(file_name, **kwargs)
        self.sort_keys = sort_keys
        file_mode = MODE_WRITE_TEXT if self.overwrite else MODE_APPEND_TEXT
        self.file = Path(self.file_name).open(file_mode, encoding="utf-8")  # noqa: SIM115

    def write(self, item: Any, flush: bool = False) -> None:
        """Write *item* as a single JSON line."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
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

    def write(self, item: Any, flush: bool = False) -> None:
        """Write *item* as a string line."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
        self.file.write(str(item) + "\n")
        self._persist_after_write()
        if flush:
            self.file.flush()


_WRITER_CLASSES: dict[str, type[ContinuousFileWriter]] = {
    "csv": CsvContinuousWriter,
    "jsonl": JsonLinesContinuousWriter,
    "txt": TextContinuousWriter,
}


class ContinuousWriter:
    """Factory that selects and manages the right writer for a file path."""

    def __init__(
        self,
        file_name: str | None = None,
        overwrite: bool = True,
        format: str | None = None,
        lazy_initialise: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialise the factory, optionally deferring writer creation."""
        self.file_name = file_name
        self.overwrite = overwrite
        self.format = format
        self.lazy_initialise = lazy_initialise
        self._writer: ContinuousFileWriter | None = None
        self._writer_kwargs = kwargs
        self._validate_extension(file_name)

        if not self.lazy_initialise:
            self._initialize_if_needed()

    @property
    def writer(self) -> ContinuousFileWriter | None:
        """Return the underlying file writer, or None if not yet initialized."""
        return self._writer

    @property
    def sort_keys(self) -> bool | None:
        """Return the configured JSON key-sorting option."""
        return self._writer_kwargs.get("sort_keys")

    @property
    def output_mode(self) -> str:
        """Return 'formatted' for text writers, 'raw' for structured writers."""
        writer = self._writer
        if isinstance(writer, TextContinuousWriter):
            return "formatted"
        return "raw"

    def is_default(self) -> bool:
        """Return True when using the text (formatted) writer."""
        return self.output_mode == "formatted"

    def is_initialised(self) -> bool:
        """Return True when the underlying writer has been created."""
        return self._writer is not None

    def initialize(self) -> None:
        """Initialize the underlying writer if needed."""
        self._initialize_if_needed()

    def _initialize_if_needed(self) -> None:
        """Create the writer, doing nothing if already initialized."""
        if self._writer is not None:
            return

        if self.file_name is None:
            msg = "File name not set"
            raise ValueError(msg)

        try:
            self._open_writer(self.file_name)
        except (OSError, csv.Error, ValueError):
            self._writer = None
            raise

    def _get_extension(self, file_name: str | None) -> str:
        """Return the file extension derived from format or file name."""
        ext = self.format
        if ext is None and file_name is not None:
            ext = Path(file_name).suffix[DOT_PREFIX_LENGTH:].lower()
        return ext or ""

    def _validate_extension(self, file_name: str | None) -> None:
        """Raise ValueError if the resolved extension is unsupported."""
        if self._get_extension(file_name) == "json":
            raise ValueError(UNSUPPORTED_JSON_EXTENSION_MESSAGE)

    def _open_writer(self, file_name: str) -> None:
        """Create parent directories, seed the file, and instantiate writer."""
        file_name = os.path.normpath(file_name)
        self._validate_extension(file_name)
        extension = self._get_extension(file_name)
        path = Path(file_name)
        if not path.exists() or self.overwrite:
            directory = path.parent
            if directory.name:
                directory.mkdir(parents=True, exist_ok=True)
            with path.open(MODE_WRITE_TEXT, encoding="utf-8"):
                pass

        writer_class = _WRITER_CLASSES.get(extension, TextContinuousWriter)
        self._writer = writer_class(
            file_name=file_name,
            overwrite=self.overwrite,
            **self._writer_kwargs,
        )

    def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
        """Write *item* using the underlying writer."""
        if self._writer is None:
            self._initialize_if_needed()

        writer = self._writer
        if writer is None:
            msg = "Writer was not initialized"
            raise RuntimeError(msg)
        writer.write(item, flush)

    def __enter__(self) -> Self:
        """Enter the context manager, returning self."""
        return self

    def close(self) -> None:
        """Close the underlying writer if initialized."""
        writer = self._writer
        if writer is not None:
            writer.close()

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Exit the context manager, closing the writer."""
        self.close()

    def __del__(self) -> None:
        """Close the writer on garbage collection, ignoring teardown errors."""
        try:
            self.close()
        except _IGNORED_DEL_EXCEPTIONS as e:
            log(
                "debug",
                "Suppressed error during garbage-collection close of "
                f"{self.file_name!r}: {e}",
            )

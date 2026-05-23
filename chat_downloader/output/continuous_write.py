# SPDX-License-Identifier: MIT

"""Continuous file writers for streaming chat messages to disk."""

import csv
import json
import os
import shutil
import time
from abc import ABC, abstractmethod
from typing import IO, Any, Self

from chat_downloader.debugging import log
from chat_downloader.output.csv_rewrite import rewrite_csv_with_new_columns
from chat_downloader.output.json_array_state import (
    find_last_non_whitespace,
    find_previous_non_whitespace,
)
from chat_downloader.utils.json_utils import flatten_json

# File operation constants
FILE_EMPTY_POSITION = 0
EXTENSION_INDEX = 1
DOT_PREFIX_LENGTH = 1

# JSON formatting constants
JSON_ARRAY_OPEN = "["
JSON_ARRAY_CLOSE = "]"
JSON_SEPARATOR_DEFAULT = ", "
INDENT_CHARACTER_DEFAULT = " "

# File modes
MODE_READ_WRITE_TEXT = "r+"
MODE_APPEND_TEXT = "a"
MODE_APPEND_PLUS_TEXT = "a+"
MODE_WRITE_TEXT = "w"
_IGNORED_DEL_EXCEPTIONS = Exception


class ContinuousFileWriter(ABC):
    """Abstract base for continuous file writers.

    Subclasses must implement write(). Provides shared close() and flush().
    """

    def __init__(
        self, file_name: str, overwrite: bool = True, **kwargs: Any
    ) -> None:
        self.file_name = file_name
        self.overwrite = overwrite
        self.file: IO[Any] | None = None

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


class JsonContinuousWriter(ContinuousFileWriter):
    """Continuously write items as a single JSON array, finalized on close."""

    def __init__(
        self,
        file_name: str,
        indent: int | None = None,
        separator: str = JSON_SEPARATOR_DEFAULT,
        indent_character: str = INDENT_CHARACTER_DEFAULT,
        sort_keys: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize a JSON-array writer for a new or existing file."""
        super().__init__(file_name, **kwargs)

        self.indent = indent
        self.separator = separator
        self.indent_character = indent_character
        self.sort_keys = sort_keys

        # Tracks opening-separator behavior.
        self._is_first = True
        self._existing_json = False
        self._existing_json_has_items = False
        self._has_items_written = False

        # Preserve prior entries when appending without loading them
        if not self.overwrite and os.path.exists(self.file_name):
            self.file = open(
                self.file_name, MODE_READ_WRITE_TEXT, encoding="utf-8"
            )
            try:
                self._configure_existing_json_array()
            except Exception:
                self.file.close()
                self.file = None
                raise
        else:
            self.file = open(self.file_name, MODE_WRITE_TEXT, encoding="utf-8")

    def _configure_existing_json_array(self) -> None:
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
        self._existing_json = True

        end_position, end_character = find_last_non_whitespace(self.file)
        if end_position is None:
            self._existing_json = False
            self._is_first = True
            self.file.seek(FILE_EMPTY_POSITION)
            return

        if end_character != JSON_ARRAY_CLOSE:
            self._recover_from_corrupted_json(
                json.JSONDecodeError("Invalid JSON array end", "", 0),
            )
            self.file.close()
            self.file = open(self.file_name, MODE_WRITE_TEXT, encoding="utf-8")
            self._existing_json = False
            return

        _, before_character = find_previous_non_whitespace(
            self.file,
            end_position - 1,
        )
        self._existing_json_has_items = before_character != JSON_ARRAY_OPEN
        self._is_first = False

        self.file.seek(end_position)

    def _recover_from_corrupted_json(self, error: json.JSONDecodeError) -> None:
        """Recover from corrupted JSON by creating a backup copy."""
        backup_path = f"{self.file_name}.corrupted.{int(time.time())}"

        try:
            with (
                open(self.file_name, "rb") as src,
                open(backup_path, "wb") as dst,
            ):
                shutil.copyfileobj(src, dst)

            log("warning", f"Corrupted JSON detected in {self.file_name}")
            log("warning", f"Backup saved to: {backup_path}")
            log(
                "warning",
                "JSON array output is not crash-safe; use JSONL for live "
                "captures to preserve completed entries after interruption.",
            )
            log("warning", f"Error details: {error}")
        except OSError as backup_error:
            log("error", f"Failed to create backup: {backup_error}")

    def _calculate_padding(self) -> str:
        """Return the indentation string based on current configuration."""
        if isinstance(self.indent, int):
            return self.indent * self.indent_character
        return self.indent if self.indent is not None else ""

    def _multiline_indent(self, text: str) -> str:
        """Add indentation to each line of multiline text."""
        padding = self._calculate_padding()
        return "".join(padding + line for line in text.splitlines(True))

    @property
    def _newline_padding(self) -> str:
        """Return a newline when indented, empty string otherwise."""
        return "\n" if self.indent is not None else ""

    def _format_item_as_json(self, item: Any) -> str:
        """Serialize *item* to JSON with configured indentation."""
        json_string = json.dumps(
            item, indent=self.indent, sort_keys=self.sort_keys
        )

        if self.indent is not None:
            return "\n" + json_string

        return json_string

    def write(self, item: Any, flush: bool = False) -> None:
        """Append an item to the output JSON array."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")

        formatted_item = self._format_item_as_json(item)

        if self._is_first:
            self.file.write(JSON_ARRAY_OPEN)
        elif self._existing_json_has_items or not self._existing_json:
            self.file.write(self.separator)

        self.file.write(formatted_item)
        self._has_items_written = True
        self._existing_json_has_items = True
        self._is_first = False

        if flush:
            self.flush()

    def close(self) -> None:
        """Finalize the JSON array and close the file."""
        if self.file and not getattr(self.file, "closed", False):
            try:
                if not self._has_items_written:
                    if not self._existing_json:
                        self.file.write(JSON_ARRAY_OPEN + JSON_ARRAY_CLOSE)
                else:
                    self.file.write(self._newline_padding + JSON_ARRAY_CLOSE)
            except OSError as e:
                log("warning", f"Error closing file {self.file_name}: {e}")
                raise
        super().close()


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
        self.file = open(
            self.file_name,
            MODE_APPEND_PLUS_TEXT,
            newline="",
            encoding="utf-8",
        )

        self.columns: list[str] = []

        if not self.overwrite:
            self._load_existing_columns()

        self._reset_csv_writer()

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
            self.csv_dict_writer.writerow(item)

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
        self.file = open(
            self.file_name,
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
        self.file = open(self.file_name, file_mode, encoding="utf-8")

    def write(self, item: Any, flush: bool = False) -> None:
        """Write *item* as a single JSON line."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
        self.file.write(json.dumps(item, sort_keys=self.sort_keys) + "\n")
        if flush:
            self.file.flush()


class TextContinuousWriter(ContinuousFileWriter):
    """Write items as plain text, one per line."""

    def __init__(self, file_name: str, **kwargs: Any) -> None:
        """Initialize a line-oriented text writer."""
        super().__init__(file_name, **kwargs)
        file_mode = MODE_WRITE_TEXT if self.overwrite else MODE_APPEND_TEXT
        self.file = open(self.file_name, file_mode, encoding="utf-8")

    def write(self, item: Any, flush: bool = False) -> None:
        """Write *item* as a string line."""
        if self.file is None:
            raise RuntimeError("File must be initialized before use")
        self.file.write(str(item) + "\n")
        if flush:
            self.file.flush()


_WRITER_CLASSES: dict[str, type[ContinuousFileWriter]] = {
    "json": JsonContinuousWriter,
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
        self.file_name = file_name
        self.overwrite = overwrite
        self.format = format
        self.lazy_initialise = lazy_initialise
        self._writer: ContinuousFileWriter | None = None
        self._writer_kwargs = kwargs

        if not self.lazy_initialise:
            self._initialize_if_needed()

    @property
    def writer(self) -> ContinuousFileWriter | None:
        """Return the underlying file writer, or None if not yet initialized."""
        return self._writer

    @property
    def indent(self) -> int | str | None:
        """Return the configured indentation passed to the writer factory."""
        return self._writer_kwargs.get("indent")

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
        except Exception:
            self._writer = None
            raise

    def _open_writer(self, file_name: str) -> None:
        """Create parent directories, seed the file, and instantiate writer."""
        file_name = os.path.normpath(file_name)
        if not os.path.exists(file_name) or self.overwrite:
            directory = os.path.dirname(file_name)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(file_name, MODE_WRITE_TEXT, encoding="utf-8"):
                pass

        extension = (
            self.format
            or os.path.splitext(file_name)[EXTENSION_INDEX][
                DOT_PREFIX_LENGTH:
            ].lower()
        )
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
        return self

    def close(self) -> None:
        """Close the underlying writer if initialized."""
        writer = self._writer
        if writer is not None:
            writer.close()

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except _IGNORED_DEL_EXCEPTIONS as e:
            log(
                "debug",
                "Suppressed error during garbage-collection close of "
                f"{self.file_name!r}: {e}",
            )

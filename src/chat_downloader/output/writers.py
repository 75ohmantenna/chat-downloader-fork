# SPDX-License-Identifier: MIT

"""Concrete continuous file writer implementations."""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Any

from chat_downloader.debugging import log

# File operation constants
MODE_APPEND_TEXT = "a"
MODE_WRITE_TEXT = "w"

# Wall-clock seconds between fsync()s. Per-record flush handles process
# crashes; fsync also survives OS-level events like power loss.
_FSYNC_INTERVAL_SECONDS = 60.0
_TAIL_SCAN_BYTES = 8192


def _repair_text_final_line(file_name: str) -> None:
    """Terminate an existing text file's final line before appending."""
    path = Path(file_name)
    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("r+b") as file:
        file.seek(-1, os.SEEK_END)
        if file.read(1) == b"\n":
            return
        file.seek(0, os.SEEK_END)
        file.write(b"\n")
        file.flush()
        os.fsync(file.fileno())
    log(
        "warning",
        f"Terminated the final text line in {file_name} before append.",
    )


def _repair_jsonl_final_line(file_name: str) -> None:
    r"""Repair a missing or crash-truncated final JSONL newline before append.

    This writer always terminates records with ``\n``. A final non-newline byte
    can therefore be either an interrupted write or a complete externally
    produced record missing its terminator. Scan backward in fixed-size chunks
    so recovery cost is independent of a multi-gigabyte log's total size.
    """
    path = Path(file_name)
    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("r+b") as file:
        end = file.seek(0, os.SEEK_END)
        file.seek(end - 1)
        if file.read(1) == b"\n":
            return

        position = end
        truncate_at = 0
        while position > 0:
            chunk_start = max(0, position - _TAIL_SCAN_BYTES)
            file.seek(chunk_start)
            chunk = file.read(position - chunk_start)
            newline_index = chunk.rfind(b"\n")
            if newline_index >= 0:
                truncate_at = chunk_start + newline_index + 1
                break
            position = chunk_start

        file.seek(truncate_at)
        tail = file.read(end - truncate_at)
        try:
            json.loads(tail)
        except (json.JSONDecodeError, UnicodeDecodeError):
            file.truncate(truncate_at)
            action = "Removed an incomplete trailing JSONL record from"
        else:
            file.seek(0, os.SEEK_END)
            file.write(b"\n")
            action = "Terminated the final JSONL record in"
        file.flush()
        os.fsync(file.fileno())
    log(
        "warning",
        f"{action} {file_name} before append.",
    )


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
        """Durably flush and close the underlying file handle if open."""
        if self.file:
            if getattr(self.file, "closed", False) is True:
                self.file = None
                return
            file = self.file
            try:
                try:
                    file.flush()
                    try:
                        os.fsync(file.fileno())
                    except (AttributeError, TypeError, ValueError) as e:
                        # In-memory and test doubles may not expose a real fd.
                        log(
                            "debug",
                            f"Final fsync() skipped on {self.file_name}: {e}",
                        )
                finally:
                    file.close()
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
        self.file.flush()
        now = time.monotonic()
        if now - self._last_fsync_monotonic < _FSYNC_INTERVAL_SECONDS:
            return
        try:
            os.fsync(self.file.fileno())
        except (AttributeError, TypeError, ValueError) as e:
            # AttributeError/ValueError: in-memory or non-fd files (tests).
            log(
                "debug",
                f"fsync() skipped on {self.file_name}: {e}",
            )
        self._last_fsync_monotonic = now


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
        if not self.overwrite:
            _repair_jsonl_final_line(self.file_name)
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
        if not self.overwrite:
            _repair_text_final_line(self.file_name)
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
    "jsonl": JsonLinesContinuousWriter,
    "txt": TextContinuousWriter,
}

# SPDX-License-Identifier: MIT

"""Concrete continuous file writer implementations."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.output._secure_file import _OutputFile
from chat_downloader.utils._bounded_records import (
    MAX_CAPTURE_RECORD_BYTES,
    CaptureRecordLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import IO, BinaryIO

# Monotonic seconds between write-triggered fsync()s. Per-record flush moves
# records into the OS; fsync requests durable storage for those written so far.
_FSYNC_INTERVAL_SECONDS = 60.0
_TAIL_SCAN_BYTES = 8192


def _newline_at(file: BinaryIO, end: int) -> str:
    """Identify the terminator ending at this byte offset."""
    file.seek(max(0, end - 2))
    return "\r\n" if file.read(min(2, end)) == b"\r\n" else "\n"


def _terminate_final_line(file: BinaryIO, end: int, newline: str) -> None:
    """Replace a partial CR terminator rather than duplicating it."""
    file.seek(end - 1)
    if file.read(1) == b"\r":
        file.truncate(end - 1)
    file.seek(0, os.SEEK_END)
    file.write(newline.encode("ascii"))


def _repair_text_final_line(file: BinaryIO, file_name: str) -> str:
    """Terminate the final line and retain the last existing newline style."""
    end = file.seek(0, os.SEEK_END)
    if not end:
        return os.linesep
    file.seek(end - 1)
    last_byte = file.read(1)
    if last_byte == b"\n":
        return _newline_at(file, end)
    newline: str = "\r\n" if last_byte == b"\r" else os.linesep
    position = end
    while position:
        start = max(0, position - _TAIL_SCAN_BYTES)
        file.seek(start)
        index = file.read(position - start).rfind(b"\n")
        if index >= 0:
            newline = _newline_at(file, start + index + 1)
            break
        position = start
    _terminate_final_line(file, end, newline)
    file.flush()
    os.fsync(file.fileno())
    log(
        "warning",
        f"Terminated the final text line in {file_name} before append.",
    )
    return newline


def _repair_jsonl_final_line(file: BinaryIO, file_name: str) -> str:
    r"""Repair a missing or crash-truncated final newline on the same handle.

    Scan at most one bounded record plus a preceding newline, then validate only
    that trailing record. Oversized tails fail without changing existing bytes.
    Never reopen an inspected destination by pathname.
    """
    end = file.seek(0, os.SEEK_END)
    if not end:
        return os.linesep
    file.seek(end - 1)
    last_byte = file.read(1)
    if last_byte == b"\n":
        return _newline_at(file, end)

    # Allow a partial CR terminator outside the record's content-byte limit.
    tail_limit = MAX_CAPTURE_RECORD_BYTES + (last_byte == b"\r")
    scan_start = max(0, end - tail_limit - 1)
    position = end
    truncate_at = 0
    while position > scan_start:
        chunk_start = max(scan_start, position - _TAIL_SCAN_BYTES)
        file.seek(chunk_start)
        chunk = file.read(position - chunk_start)
        newline_index = chunk.rfind(b"\n")
        if newline_index >= 0:
            truncate_at = chunk_start + newline_index + 1
            break
        position = chunk_start

    if end - truncate_at > tail_limit:
        raise CaptureRecordLimitError
    newline = (
        _newline_at(file, truncate_at)
        if truncate_at
        else "\r\n"
        if last_byte == b"\r"
        else os.linesep
    )

    file.seek(truncate_at)
    tail = file.read(end - truncate_at)
    try:
        json.loads(tail)
    except (json.JSONDecodeError, UnicodeDecodeError):
        file.truncate(truncate_at)
        action = "Removed an incomplete trailing JSONL record from"
    else:
        _terminate_final_line(file, end, newline)
        action = "Terminated the final JSONL record in"
    file.flush()
    os.fsync(file.fileno())
    log(
        "warning",
        f"{action} {file_name} before append.",
    )
    return newline


def _assert_utc_aware(item: Any) -> None:
    """Give top-level naive datetimes a specific serialization error.

    JSON does not encode ``datetime`` objects. This cheap scan explains the
    particularly ambiguous naive case before the standard JSON encoder rejects
    other unsupported values. Callers must convert datetimes to UTC integer
    microseconds or strings before writing.
    """
    if not isinstance(item, dict):
        return
    for key, value in item.items():
        if isinstance(value, dt.datetime) and value.tzinfo is None:
            msg = (
                f"Output field {key!r} contains a naive datetime "
                f"({value!r}); JSONL accepts only JSON-compatible values. "
                "Convert timestamps to UTC integer microseconds or ISO 8601 "
                "strings before writing."
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
        _prepared_file: _OutputFile | None = None,
        _defer_initialization: bool = False,
    ) -> None:
        """Initialize the writer for the given file path."""
        self.file_name = file_name
        self.overwrite = overwrite
        self.file: IO[Any] | None = None
        self._last_fsync_monotonic = time.monotonic()
        self._prepared_file = _prepared_file
        self._defer_initialization = _defer_initialization
        self._pending_file: BinaryIO | None = None
        self._repair_final_line: Callable[[BinaryIO, str], str] | None = None

    def _open_file(self, repair: Callable[[BinaryIO, str], str]) -> None:
        """Transfer a validated stream, optionally deferring destructive setup."""
        prepared = self._prepared_file or _OutputFile(
            self.file_name, append=not self.overwrite, exclusive=False
        )
        self._prepared_file = prepared
        try:
            self._pending_file = prepared.detach()
            self._repair_final_line = repair
            if not self._defer_initialization:
                prepared.validate(self._pending_file)
                self._initialize_file()
        except BaseException:
            self.close()
            prepared.close()
            self._prepared_file = None
            raise

    def _initialize_file(self) -> None:
        """Commit an already-validated, pinned stream without namespace lookups."""
        stream = self._pending_file
        prepared = self._prepared_file
        repair = self._repair_final_line
        if stream is None or prepared is None or repair is None:
            return
        self._pending_file = None
        self._prepared_file = None
        self._repair_final_line = None
        try:
            newline: str = os.linesep
            if self.overwrite:
                stream.truncate(0)
                stream.seek(0)
            else:
                newline = repair(stream, self.file_name)
                stream.seek(0, os.SEEK_END)
            self.file = io.TextIOWrapper(stream, encoding="utf-8", newline=newline)
        except BaseException:
            stream.close()
            raise
        finally:
            prepared.close()

    def close(self) -> None:
        """Durably flush and close the underlying file handle if open."""
        pending = getattr(self, "_pending_file", None)
        if pending is not None:
            self._pending_file = None
            try:
                pending.close()
            finally:
                prepared, self._prepared_file = self._prepared_file, None
                if prepared is not None:
                    prepared.close()
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

        Flush transfers Python-buffered bytes to the OS; fsync requests durable
        storage. Each write checks elapsed time before synchronizing, and close
        synchronizes again. An interruption can still tear an in-progress record.
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
    """Write UTF-8 JSON values as flushed lines with periodic synchronization."""

    def __init__(
        self,
        file_name: str,
        *,
        sort_keys: bool = True,
        overwrite: bool = True,
        _prepared_file: _OutputFile | None = None,
        _defer_initialization: bool = False,
    ) -> None:
        """Initialize a JSON Lines writer."""
        super().__init__(
            file_name,
            overwrite=overwrite,
            _prepared_file=_prepared_file,
            _defer_initialization=_defer_initialization,
        )
        self.sort_keys = sort_keys
        self._open_file(_repair_jsonl_final_line)

    def write(self, item: Any, *, flush: bool = False) -> None:
        """Write *item* as a single JSON line."""
        if self.file is None:
            msg = "File must be initialized before use"
            raise RuntimeError(msg)
        _assert_utc_aware(item)
        self.file.write(
            json.dumps(item, sort_keys=self.sort_keys, allow_nan=False) + "\n"
        )
        self._persist_after_write()
        if flush:
            self.file.flush()


class TextContinuousWriter(ContinuousFileWriter):
    """Write stringified items followed by a newline."""

    def __init__(
        self,
        file_name: str,
        *,
        overwrite: bool = True,
        _prepared_file: _OutputFile | None = None,
        _defer_initialization: bool = False,
    ) -> None:
        """Initialize a line-oriented text writer."""
        super().__init__(
            file_name,
            overwrite=overwrite,
            _prepared_file=_prepared_file,
            _defer_initialization=_defer_initialization,
        )
        self._open_file(_repair_text_final_line)

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

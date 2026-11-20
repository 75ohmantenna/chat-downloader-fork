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
from chat_downloader.output._append_tail import (
    _FileActivation,
    _inspect_jsonl_final_line,
    _inspect_text_final_line,
)
from chat_downloader.output._secure_file import _OutputFile

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import IO, BinaryIO

_FSYNC_INTERVAL_SECONDS = 60.0


def _assert_utc_aware(item: Any) -> None:
    """Explain ambiguous naive datetimes before JSON serialization fails."""
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
        self._activation: _FileActivation | None = None

    def _open_file(
        self, inspect_tail: Callable[[BinaryIO, str], _FileActivation]
    ) -> None:
        """Transfer a validated stream, optionally deferring destructive setup."""
        prepared = self._prepared_file or _OutputFile(
            self.file_name, append=not self.overwrite, exclusive=False
        )
        self._prepared_file = prepared
        try:
            self._pending_file = prepared.detach()
            self._activation = (
                _FileActivation(truncate_at=0)
                if self.overwrite
                else _FileActivation.inspect_append(
                    self._pending_file, self.file_name, inspect_tail
                )
            )
            if not self._defer_initialization:
                prepared.validate(self._pending_file)
                self._initialize_file()
                self._commit_reservation()
        except BaseException:
            self.close()
            raise

    def _initialize_file(self) -> None:
        """Activate an already-validated, pinned stream."""
        stream = self._pending_file
        activation = self._activation
        if (
            stream is None or activation is None
        ):  # pragma: no cover — only transferred writers activate
            return
        activation.apply(stream, self.file_name)
        self.file = io.TextIOWrapper(
            stream, encoding="utf-8", newline=activation.newline
        )
        self._pending_file = None
        self._activation = None

    def _commit_reservation(self) -> None:
        """Release the namespace guard after the group has activated."""
        prepared, self._prepared_file = self._prepared_file, None
        if prepared is not None:
            prepared.close()

    def close(self) -> None:
        """Durably flush and close the underlying file handle if open."""
        prepared = getattr(self, "_prepared_file", None)
        try:
            if prepared is not None and prepared.directory_fd is not None:
                prepared.discard_empty_reservation()
            self._close_stream()
        finally:
            if prepared is not None:
                self._prepared_file = None
                prepared.close(discard=True)

    def _close_stream(self) -> None:
        """Close the pending binary stream or the activated text stream."""
        pending = getattr(self, "_pending_file", None)
        if pending is not None:
            self._pending_file = None
            pending.close()
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
        self._open_file(_inspect_jsonl_final_line)

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
        self._open_file(_inspect_text_final_line)

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

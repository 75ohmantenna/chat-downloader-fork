# SPDX-License-Identifier: MIT

"""Continuous file writers for streaming chat messages to disk."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from chat_downloader.debugging import log
from chat_downloader.output.writers import (
    _WRITER_CLASSES,
    MODE_WRITE_TEXT,
    ContinuousFileWriter,
    CsvContinuousWriter,
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)

__all__ = [
    "ContinuousFileWriter",
    "ContinuousWriter",
    "CsvContinuousWriter",
    "JsonLinesContinuousWriter",
    "TextContinuousWriter",
]

if TYPE_CHECKING:
    import types

DOT_PREFIX_LENGTH = 1
_IGNORED_DEL_EXCEPTIONS = Exception
UNSUPPORTED_JSON_EXTENSION_MESSAGE = (
    "JSON array output is no longer supported. Use a .jsonl output path "
    "for structured chat output."
)


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

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: types.TracebackType | None,
    ) -> None:
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

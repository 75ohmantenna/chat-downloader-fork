# SPDX-License-Identifier: MIT

"""Continuous file writers for streaming chat messages to disk."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from chat_downloader.debugging import log
from chat_downloader.output._secure_file import _OutputFile
from chat_downloader.output.writers import (
    _WRITER_CLASSES,
    ContinuousFileWriter,
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)

__all__ = [
    "ContinuousFileWriter",
    "ContinuousWriter",
    "JsonLinesContinuousWriter",
    "TextContinuousWriter",
]

if TYPE_CHECKING:
    import types

DOT_PREFIX_LENGTH = 1
_IGNORED_DEL_EXCEPTIONS = Exception
SUPPORTED_OUTPUT_FORMATS = frozenset(_WRITER_CLASSES)
UNSUPPORTED_OUTPUT_FORMAT_MESSAGE = (
    "Unsupported output format {extension!r}. Use a .jsonl or .txt output path."
)


class ContinuousWriter:
    """Factory that selects and manages the right writer for a file path."""

    def __init__(
        self,
        file_name: str | None = None,
        *,
        overwrite: bool = True,
        format: str | None = None,  # noqa: A002 — public ContinuousWriter API; renaming would break callers
        lazy_initialise: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the factory, optionally deferring writer creation."""
        self.file_name = file_name
        self.overwrite = overwrite
        self.format = format
        self.lazy_initialise = lazy_initialise
        self._writer: ContinuousFileWriter | None = None
        self._prepared_file: _OutputFile | None = None
        self._file_identity: tuple[int, int] | None = None
        self._require_new_file = False
        self._defer_initialization = False
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
        except BaseException:
            self._writer = None
            if self._prepared_file is not None:
                prepared, self._prepared_file = self._prepared_file, None
                prepared.close()
            raise

    def _get_extension(self, file_name: str | None) -> str:
        """Return the file extension derived from format or file name."""
        ext = self.format
        if ext is None and file_name is not None:
            ext = Path(file_name).suffix[DOT_PREFIX_LENGTH:].lower()
        return ext.lower() if ext else ""

    def _validate_extension(self, file_name: str | None) -> None:
        """Raise ValueError if the path or explicit format is unsupported."""
        if file_name is None:
            return
        path_extension = Path(file_name).suffix[DOT_PREFIX_LENGTH:].lower()
        extensions = [path_extension]
        if self.format is not None:
            extensions.append(self.format.lower())
        for extension in extensions:
            if extension not in SUPPORTED_OUTPUT_FORMATS:
                raise ValueError(
                    UNSUPPORTED_OUTPUT_FORMAT_MESSAGE.format(extension=extension)
                )
        if self.format is not None and path_extension != self.format.lower():
            msg = (
                f"Output format {self.format!r} does not match file extension "
                f"{path_extension!r}."
            )
            raise ValueError(msg)

    def _prepare_output(self) -> None:
        """Acquire and validate a handle without repairing or truncating it."""
        if self._writer is not None or self._prepared_file is not None:
            return
        if self.file_name is None:
            msg = "File name not set"
            raise ValueError(msg)
        self._validate_extension(self.file_name)
        self._prepared_file = _OutputFile(
            self.file_name,
            append=not self.overwrite,
            exclusive=self._require_new_file,
        )

    def _open_writer(self, file_name: str) -> None:
        """Instantiate the concrete writer using the preflighted handle."""
        self._prepare_output()
        prepared, self._prepared_file = self._prepared_file, None
        writer_class = _WRITER_CLASSES[self._get_extension(file_name)]
        try:
            self._writer = writer_class(
                file_name=file_name,
                overwrite=self.overwrite,
                _prepared_file=prepared,
                _defer_initialization=self._defer_initialization,
                **self._writer_kwargs,
            )
            if prepared is not None:
                self._file_identity = (prepared.info.st_dev, prepared.info.st_ino)
        except BaseException:
            if prepared is not None:
                prepared.close()
            raise

    def write(self, item: dict[str, Any] | str, *, flush: bool = False) -> None:
        """Write *item* using the underlying writer."""
        if self._writer is None:
            self._initialize_if_needed()

        writer = self._writer
        if writer is None:
            msg = "Writer was not initialized"
            raise RuntimeError(msg)
        writer.write(item, flush=flush)

    def __enter__(self) -> Self:
        """Enter the context manager, returning self."""
        return self

    def close(self) -> None:
        """Close the underlying writer if initialized."""
        writer = self._writer
        if self._prepared_file is not None:
            prepared, self._prepared_file = self._prepared_file, None
            prepared.close()
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

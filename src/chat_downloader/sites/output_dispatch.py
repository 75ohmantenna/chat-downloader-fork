# SPDX-License-Identifier: MIT

"""Writer setup, item dispatch, and shutdown for a Chat's outputs."""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from string import Formatter
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log
from chat_downloader.output._secure_file import _check_output_path
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.utils.filename_utils import (
    output_path_identity,
    sanitize_filename_component,
    validate_output_template,
)

from ._message_dedup import _FormattedMessageDeduplicator

if TYPE_CHECKING:
    from collections.abc import Generator


class ChatOutputWriter(Protocol):
    """Structural writer interface used by the chat output dispatcher."""

    @property
    def file_name(self) -> str | None:
        """Return the configured name, which may await lazy configuration."""
        ...  # pragma: no cover — structural typing declaration

    @file_name.setter
    def file_name(self, value: str) -> None:
        """Set the resolved output path before opening the writer."""
        ...  # pragma: no cover — structural typing declaration

    @property
    def output_mode(self) -> str:
        """Return the writer's resolved file format."""
        ...  # pragma: no cover — structural typing declaration

    def is_initialised(self) -> bool:
        """Return True if the writer has been initialized."""
        ...  # pragma: no cover — structural typing declaration

    def initialize(self) -> None:
        """Initialize the writer, creating files and resources as needed."""
        ...  # pragma: no cover — structural typing declaration

    def write(self, item: dict[str, Any] | str, *, flush: bool = False) -> None:
        """Write a chat item to the output target."""
        ...  # pragma: no cover — structural typing declaration

    def close(self) -> None:
        """Close the writer and release any held resources."""
        ...  # pragma: no cover — structural typing declaration


class _ChatHost(Protocol):
    """Narrow interface the dispatcher requires from its Chat host."""

    title: str | None
    id: str | None

    def format(self, item: dict[str, Any]) -> str:
        """Render one chat item with the currently configured formatter."""
        ...  # pragma: no cover — structural typing declaration


class _WriterSummary(TypedDict):
    """Debug-facing creation state and record count for one output."""

    file_name: str
    file_created: bool
    records_written: int


def _expand_output_file_name(
    file_name: str,
    *,
    title: str | None,
    video_id: str | None,
) -> str:
    """Expand output placeholders with safe single-component metadata."""
    validate_output_template(file_name)
    safe_title = sanitize_filename_component(title).replace("..", "_")
    safe_id = sanitize_filename_component(video_id).replace("..", "_")
    return file_name.format(title=safe_title, id=safe_id)


def _register_output_path(
    file_name: str,
    identity: tuple[object, ...],
    seen: dict[tuple[object, ...], str],
) -> bool:
    """Register one captured identity for both alias grouping and policy."""
    suffix = file_name.rsplit(".", 1)[-1].lower()
    if identity in seen:
        if seen[identity] != suffix:
            msg = "Different output formats require distinct files."
            raise ValueError(msg)
        log("warning", f"Duplicate output path '{file_name}' — skipping.")
        return False
    seen[identity] = suffix
    return True


class _ChatOutputDispatcher:
    """Manage writer setup, callback dispatch, deduplication, and chat shutdown.

    Superchat/ticker deduplication is an output concern (it prevents the same
    paid item from being written twice via the chat and ticker surfaces), so the
    bounded seen-id cache lives here rather than on the result model.
    """

    def __init__(
        self,
        chat: _ChatHost,
        max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
    ) -> None:
        self._chat = chat
        self.writers: list[ChatOutputWriter] = []
        self._attached_writer_ids: set[int] = set()
        self._initialised_writer_ids: set[int] = set()
        self._records_written_by_writer: dict[int, int] = {}
        self._resolved_writer_ids: set[int] = set()
        self._generated_writer_ids: set[int] = set()
        self._record_snapshots: dict[int, tuple[int, int]] | None = None
        self._formatted_duplicates_suppressed = 0
        self._write_error_count: int = 0
        self._record_recovery_failed = False
        self.closed = False
        self._formatted_deduplicator = _FormattedMessageDeduplicator(
            max_seen_message_ids,
        )

    def _initialise_writers(self) -> None:
        """Initialize each attached writer once before dispatch begins."""
        self._resolve_writer_paths()
        pending = [
            writer
            for writer in self.writers
            if id(writer) not in self._initialised_writer_ids
        ]
        try:
            for writer in pending:
                if isinstance(writer, ContinuousWriter):
                    writer._prepare_output()
            self._snapshot_records()
            self._transfer_writers(pending)
            # Collective validation is the commit boundary. Activation below
            # uses pinned descriptors, never another rejecting namespace lookup.
            for writer in pending:
                if isinstance(writer, ContinuousWriter) and writer.writer is not None:
                    writer.writer._initialize_file()
                self._initialised_writer_ids.add(id(writer))
        except BaseException:
            self.close()
            raise
        finally:
            for writer in pending:
                if isinstance(writer, ContinuousWriter):
                    writer._defer_initialization = False

    def _transfer_writers(self, pending: list[ChatOutputWriter]) -> None:
        """Transfer and validate all streams before any destructive setup."""
        for writer in pending:
            if not writer.is_initialised():
                log("debug", f"Writing to file: {writer.file_name}")
                if isinstance(writer, ContinuousWriter):
                    writer._defer_initialization = True
                writer.initialize()
        for writer in pending:
            if isinstance(writer, ContinuousWriter) and writer.writer is not None:
                concrete = writer.writer
                if concrete._prepared_file is not None:
                    concrete._prepared_file.validate(concrete._pending_file)

    @staticmethod
    def _writer_name(writer: ChatOutputWriter) -> str:
        """Require a configured name without assuming factory initialization."""
        file_name = writer.file_name
        if file_name is None:
            msg = "File name not set"
            raise ValueError(msg)
        return file_name

    def _writer_path(self, writer: ChatOutputWriter) -> str:
        """Expand each original template once, distinguishing escaped braces."""
        writer_id = id(writer)
        file_name = self._writer_name(writer)
        if writer.is_initialised() or writer_id in self._resolved_writer_ids:
            return file_name
        path = _expand_output_file_name(
            file_name, title=self._chat.title, video_id=self._chat.id
        )
        if (isinstance(writer, ContinuousWriter) and writer._require_new_file) or any(
            field is not None
            for _literal, field, _spec, _conversion in Formatter().parse(file_name)
        ):
            self._generated_writer_ids.add(writer_id)
        return path

    def _resolve_writer_paths(self) -> None:
        """Preflight every final path and propagate exclusive alias policies."""
        if all(writer.is_initialised() for writer in self.writers):
            return
        paths = [(writer, self._writer_path(writer)) for writer in self.writers]
        identities = {id(writer): output_path_identity(path) for writer, path in paths}
        generated = {
            identities[id(writer)]
            for writer, _path in paths
            if id(writer) in self._generated_writer_ids
        }
        seen: dict[tuple[object, ...], str] = {}
        resolved = []
        for writer, path in paths:
            unique = _register_output_path(path, identities[id(writer)], seen)
            if writer.is_initialised() or unique:
                resolved.append((writer, path))
        for writer, path in paths:
            if not writer.is_initialised() and isinstance(writer, ContinuousWriter):
                _check_output_path(
                    path, exclusive=id(writer) in self._generated_writer_ids
                )
        self.writers = [writer for writer, _path in resolved]
        for writer, path in resolved:
            writer.file_name = path
            self._resolved_writer_ids.add(id(writer))
            if isinstance(writer, ContinuousWriter):
                writer._require_new_file = (
                    writer._require_new_file or identities[id(writer)] in generated
                )

    def attach_writer(self, writer: ChatOutputWriter) -> None:
        """Attach a writer to the chat, ignoring repeated object attachment."""
        writer_id = id(writer)
        if writer_id in self._attached_writer_ids:
            return
        self.writers.append(writer)
        self._attached_writer_ids.add(writer_id)
        self._records_written_by_writer[writer_id] = 0

    def emit(self, item: dict[str, Any]) -> None:
        """Write a chat item to all configured outputs."""
        if not self.writers:
            return

        self._initialise_writers()
        formatted_item: str | None = None
        emit_formatted: bool | None = None
        for writer in self.writers:
            if writer.output_mode != "formatted":
                writer.write(item, flush=True)
                self._records_written_by_writer[id(writer)] += 1
                continue

            if emit_formatted is None:
                emit_formatted = self._formatted_deduplicator.should_emit(item)
                if not emit_formatted:
                    self._formatted_duplicates_suppressed += 1
            if not emit_formatted:
                continue
            if formatted_item is None:
                formatted_item = self._chat.format(item)
            writer.write(formatted_item, flush=True)
            self._records_written_by_writer[id(writer)] += 1

    def _snapshot_records(self) -> None:
        """Hold independent descriptors so shutdown cannot redirect rollback."""
        if self._record_snapshots is None:
            return
        for writer in self.writers:
            if id(writer) in self._record_snapshots:
                continue
            if not isinstance(writer, ContinuousWriter):
                msg = "Checkpoint rollback requires descriptor-backed output writers."
                raise TypeError(msg)
            concrete = writer.writer
            prepared = writer._prepared_file
            stream = (
                concrete.file
                if concrete is not None
                else prepared.stream
                if prepared is not None
                else None
            )
            if stream is not None:
                size = os.fstat(stream.fileno()).st_size
                self._record_snapshots[id(writer)] = (os.dup(stream.fileno()), size)

    @contextmanager
    def record_transaction(self) -> Generator[None, None, None]:
        """Restore descriptor-pinned prefixes/counts after a torn emission.

        Checkpointed iteration stops after a failed record, so the formatted
        deduplication cache is discarded with the capture rather than reused.
        """
        self._record_snapshots = {}
        counts = dict(self._records_written_by_writer)
        duplicates = self._formatted_duplicates_suppressed
        try:
            self._snapshot_records()
            yield
        except StopIteration:
            raise
        except BaseException:
            try:
                for descriptor, previous_size in self._record_snapshots.values():
                    os.ftruncate(descriptor, previous_size)
                    os.fsync(descriptor)
            except BaseException:
                self._record_recovery_failed = True
                raise
            self._records_written_by_writer = counts
            self._formatted_duplicates_suppressed = duplicates
            raise
        finally:
            for descriptor, _size in self._record_snapshots.values():
                with suppress(OSError):
                    os.close(descriptor)
            self._record_snapshots = None

    def close(self) -> None:
        """Close every writer once, then propagate the first unexpected error."""
        if self.closed:
            return

        self.closed = True
        first_error: Exception | None = None
        for writer in self.writers:
            try:
                writer.close()
            except (OSError, RuntimeError) as error:
                self._write_error_count += 1
                log(
                    "warning",
                    "Suppressed close() error while finalizing output writer "
                    f"for {self._chat.title!r}: {error}",
                )
            except Exception as error:  # noqa: BLE001 - finish all owned cleanup
                self._write_error_count += 1
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    @property
    def write_error_count(self) -> int:
        """Return the number of writer close errors encountered."""
        return self._write_error_count

    @property
    def formatted_duplicates_suppressed(self) -> int:
        """Return provider items omitted from all formatted file outputs."""
        return self._formatted_duplicates_suppressed

    def counts_match(self, raw_count: int) -> bool:
        """Require successful recovery and each writer's expected record count."""
        return not self._record_recovery_failed and all(
            self._records_written_by_writer[id(writer)]
            == (
                raw_count - self._formatted_duplicates_suppressed
                if writer.output_mode == "formatted"
                else raw_count
            )
            for writer in self.writers
        )

    @property
    def writer_summaries(self) -> list[_WriterSummary]:
        """Return successful record counts for attached output writers."""
        return [
            {
                "file_name": self._writer_name(writer),
                "file_created": writer.is_initialised(),
                "records_written": self._records_written_by_writer[id(writer)],
            }
            for writer in self.writers
        ]

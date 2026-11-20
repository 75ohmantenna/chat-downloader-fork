# SPDX-License-Identifier: MIT

"""Writer setup, item dispatch, and shutdown for a Chat's outputs."""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.output.targets import ChatOutputWriter, _OutputTargets
from chat_downloader.output.writers import TextContinuousWriter

from ._message_dedup import _FormattedMessageDeduplicator

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from chat_downloader.output.writers import ContinuousFileWriter
    from chat_downloader.sites.models import Chat
    from chat_downloader.utils.json_types import JSONDict


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
        self._targets = _OutputTargets()
        self.writers = self._targets.writers
        self._initialised_writer_ids: set[int] = set()
        self._records_written_by_writer: dict[int, int] = {}
        self._record_snapshots: dict[int, tuple[int, int]] | None = None
        self._formatted_duplicates_suppressed = 0
        self._write_error_count: int = 0
        self._record_recovery_failed = False
        self.closed = False
        self._formatted_deduplicator = _FormattedMessageDeduplicator(
            max_seen_message_ids,
        )

    def _initialise_writers(self, item: JSONDict) -> tuple[str | None, bool | None]:
        """Prepare outputs and render the initial record before activation."""
        formatted: str | None = None
        emit_formatted: bool | None = None
        self._targets.resolve(title=self._chat.title, video_id=self._chat.id)
        pending = [
            writer
            for writer in self.writers
            if id(writer) not in self._initialised_writer_ids
        ]
        try:
            for writer in pending:
                if isinstance(writer, ContinuousWriter):
                    writer._prepare_output()
            concrete_writers = self._transfer_writers(pending)
            if self._has_pending_formatted_output(concrete_writers):
                emit_formatted = self._formatted_deduplicator.should_emit(item)
                if emit_formatted:
                    formatted = self._chat.format(item)
            # All namespace and tail checks succeeded. Activation uses the
            # same descriptors and can still raise on filesystem I/O.
            for concrete in concrete_writers:
                concrete._initialize_file()
            for concrete in concrete_writers:
                concrete._commit_reservation()
            self._initialised_writer_ids.update(map(id, pending))
        except BaseException:
            self.close()
            raise
        finally:
            for writer in pending:
                if isinstance(writer, ContinuousWriter):
                    writer._defer_initialization = False
        return formatted, emit_formatted

    def _has_pending_formatted_output(
        self, concrete_writers: list[ContinuousFileWriter]
    ) -> bool:
        """Pre-render when a built-in group will activate a formatted output."""
        return any(
            isinstance(concrete, TextContinuousWriter) for concrete in concrete_writers
        ) and all(isinstance(writer, ContinuousWriter) for writer in self.writers)

    def _transfer_writers(
        self, pending: list[ChatOutputWriter]
    ) -> list[ContinuousFileWriter]:
        """Transfer and validate all streams before any destructive setup."""
        for writer in pending:
            if not writer.is_initialised():
                log("debug", f"Writing to file: {writer.file_name}")
                if isinstance(writer, ContinuousWriter):
                    writer._defer_initialization = True
                writer.initialize()
        concrete_writers = []
        for writer in pending:
            if isinstance(writer, ContinuousWriter) and writer.writer is not None:
                concrete = writer.writer
                if concrete._prepared_file is not None:
                    concrete._prepared_file.validate(concrete._pending_file)
                    concrete_writers.append(concrete)
        return concrete_writers

    def output_paths(self) -> list[Path]:
        """Observe selected output paths without opening files or freezing metadata."""
        return self._targets.paths(title=self._chat.title, video_id=self._chat.id)

    def attach_writer(self, writer: ChatOutputWriter) -> None:
        """Attach a writer to the chat, ignoring repeated object attachment."""
        writer_id = id(writer)
        if not self._targets.attach(writer):
            return
        self._records_written_by_writer[writer_id] = 0

    def emit(self, item: dict[str, Any]) -> None:
        """Write a chat item to all configured outputs."""
        if not self.writers:
            return

        formatted_item, emit_formatted = self._initialise_writers(item)
        if self._record_snapshots is not None:
            self._snapshot_records(self._record_snapshots)
            self._write_item(item, formatted_item, emit_formatted=emit_formatted)
        elif len(self.writers) > 1 and all(
            isinstance(writer, ContinuousWriter) for writer in self.writers
        ):
            with self.record_transaction():
                try:
                    self._write_item(
                        item, formatted_item, emit_formatted=emit_formatted
                    )
                except BaseException:
                    # Closing can flush buffered bytes. Finish it before
                    # restoring the prefixes through the duplicated descriptors.
                    with suppress(Exception):
                        self.close()
                    raise
        else:
            self._write_item(item, formatted_item, emit_formatted=emit_formatted)

    def _write_item(
        self,
        item: JSONDict,
        formatted_item: str | None,
        *,
        emit_formatted: bool | None,
    ) -> None:
        """Dispatch one item after output activation."""
        if emit_formatted is False:
            self._formatted_duplicates_suppressed += 1
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

    def _snapshot_records(self, snapshots: dict[int, tuple[int, int]]) -> None:
        """Hold independent descriptors so shutdown cannot redirect rollback."""
        for writer in self.writers:
            if id(writer) in snapshots:
                continue
            if not isinstance(writer, ContinuousWriter):
                msg = "Checkpoint rollback requires descriptor-backed output writers."
                raise TypeError(msg)
            concrete = writer.writer
            stream = concrete.file if concrete is not None else None
            if stream is not None:
                size = os.fstat(stream.fileno()).st_size
                snapshots[id(writer)] = (os.dup(stream.fileno()), size)

    @contextmanager
    def record_transaction(self) -> Generator[None, None, None]:
        """Restore descriptor-pinned prefixes/counts after a torn emission.

        Iteration stops after a failed record, so the formatted deduplication
        cache is discarded with the capture rather than reused.
        """
        snapshots: dict[int, tuple[int, int]] = {}
        self._record_snapshots = snapshots
        counts = dict(self._records_written_by_writer)
        duplicates = self._formatted_duplicates_suppressed
        try:
            self._snapshot_records(snapshots)
            yield
        except StopIteration:
            raise
        except BaseException:
            try:
                for descriptor, previous_size in snapshots.values():
                    os.ftruncate(descriptor, previous_size)
                    os.fsync(descriptor)
            except BaseException:
                self._record_recovery_failed = True
                raise
            self._records_written_by_writer = counts
            self._formatted_duplicates_suppressed = duplicates
            raise
        finally:
            for descriptor, _size in snapshots.values():
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
                "file_name": self._targets.name(writer),
                "file_created": writer.is_initialised(),
                "records_written": self._records_written_by_writer[id(writer)],
            }
            for writer in self.writers
        ]


def configure_outputs(
    chat: Chat,
    outputs: str | list[str] | None,
    *,
    overwrite: bool,
    sort_keys: bool,
) -> None:
    """Attach a provisionally selected lazy group without changing existing writers."""
    if not outputs:
        return
    targets = _OutputTargets()
    for name in outputs if isinstance(outputs, list) else [outputs]:
        targets.attach(
            ContinuousWriter(
                name, overwrite=overwrite, sort_keys=sort_keys, lazy_initialise=True
            )
        )
    targets.resolve(
        title=chat.title,
        video_id=chat.id,
        provisional=True,
        metadata_pending=chat._metadata_pending,
    )
    for writer in targets.writers:
        chat.attach_writer(writer)

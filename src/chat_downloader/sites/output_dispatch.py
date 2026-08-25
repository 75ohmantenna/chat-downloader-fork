# SPDX-License-Identifier: MIT

"""Writer setup, item dispatch, and shutdown for a Chat's outputs."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log
from chat_downloader.utils.filename_utils import sanitize_filename_component

from ._message_dedup import _FormattedMessageDeduplicator


class ChatOutputWriter(Protocol):
    """Structural writer interface used by the chat output dispatcher."""

    file_name: str
    output_mode: str

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
    """Debug-facing count of records successfully written to one output."""

    file_name: str
    records_written: int


def _expand_output_file_name(
    file_name: str,
    *,
    title: str | None,
    video_id: str | None,
) -> str:
    """Expand output placeholders with safe single-component metadata."""
    safe_title = sanitize_filename_component(title).replace("..", "_")
    safe_id = sanitize_filename_component(video_id).replace("..", "_")
    return file_name.format(title=safe_title, id=safe_id)


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
        self._write_error_count: int = 0
        self.closed = False
        self._formatted_deduplicator = _FormattedMessageDeduplicator(
            max_seen_message_ids,
        )

    def _initialise_writers(self) -> None:
        """Initialize each attached writer once before dispatch begins."""
        for writer in self.writers:
            writer_id = id(writer)
            if writer_id in self._initialised_writer_ids:
                continue

            if not writer.is_initialised():
                writer.file_name = _expand_output_file_name(
                    writer.file_name,
                    title=self._chat.title,
                    video_id=self._chat.id,
                )
                log("debug", f"Writing to file: {writer.file_name}")
                writer.initialize()

            self._initialised_writer_ids.add(writer_id)

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
                continue
            if formatted_item is None:
                formatted_item = self._chat.format(item)
            writer.write(formatted_item, flush=True)
            self._records_written_by_writer[id(writer)] += 1

    def close(self) -> None:
        """Close all attached writers once and log any cleanup failures."""
        if self.closed:
            return

        self.closed = True
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

    @property
    def write_error_count(self) -> int:
        """Return the number of writer close errors encountered."""
        return self._write_error_count

    @property
    def writer_summaries(self) -> list[_WriterSummary]:
        """Return successful record counts for attached output writers."""
        return [
            {
                "file_name": writer.file_name,
                "records_written": self._records_written_by_writer[id(writer)],
            }
            for writer in self.writers
        ]

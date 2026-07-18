# SPDX-License-Identifier: MIT

"""Writer setup, item dispatch, and shutdown for a Chat's outputs."""

from __future__ import annotations

from typing import Any, Protocol

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
        ...

    def initialize(self) -> None:
        """Initialize the writer, creating files and resources as needed."""
        ...

    def write(self, item: dict[str, Any] | str, *, flush: bool = False) -> None:
        """Write a chat item to the output target."""
        ...

    def close(self) -> None:
        """Close the writer and release any held resources."""
        ...


class _ChatHost(Protocol):
    """Narrow interface the dispatcher requires from its Chat host."""

    title: str | None
    id: str | None

    def format(self, item: dict[str, Any]) -> str:
        """Render one chat item with the currently configured formatter."""
        ...


class _ChatOutputDispatcher:
    """Manage writer setup, callback dispatch, dedup, and shutdown for a chat.

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
        self._write_error_count: int = 0
        self.closed = False
        self._formatted_deduplicator = _FormattedMessageDeduplicator(
            max_seen_message_ids,
        )

    def _initialise_writers(self) -> None:
        """Initialise each attached writer once before dispatch begins."""
        for writer in self.writers:
            writer_id = id(writer)
            if writer_id in self._initialised_writer_ids:
                continue

            if not writer.is_initialised():
                safe_title = sanitize_filename_component(self._chat.title).replace(
                    "..", "_"
                )
                safe_id = sanitize_filename_component(self._chat.id).replace("..", "_")
                writer.file_name = writer.file_name.format(
                    title=safe_title,
                    id=safe_id,
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
                continue

            if emit_formatted is None:
                emit_formatted = self._formatted_deduplicator.should_emit(item)
            if not emit_formatted:
                continue
            if formatted_item is None:
                formatted_item = self._chat.format(item)
            writer.write(formatted_item, flush=True)

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

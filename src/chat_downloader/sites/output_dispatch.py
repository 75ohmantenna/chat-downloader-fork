# SPDX-License-Identifier: MIT

"""Writer setup, callback dispatch, and shutdown for a Chat's outputs."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Any, Protocol

from chat_downloader.debugging import log
from chat_downloader.utils.filename_utils import sanitize_filename_component

if TYPE_CHECKING:
    from collections.abc import Callable

# Message types that should be deduplicated (superchat-related messages
# that appear in both chat and ticker)
SUPERCHAT_DEDUP_TYPES = frozenset(
    {
        "paid_message",
        "ticker_paid_message_item",
        "paid_sticker",
        "ticker_paid_sticker_item",
        "membership_item",
        "ticker_sponsor_item",
    },
)


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

    def _register_seen_message_id(self, message_id: str) -> bool:
        """Register a message ID for deduplication; return True if new."""
        ...


class _ChatOutputDispatcher:
    """Manage writer setup, callback dispatch, and shutdown for a chat."""

    def __init__(self, chat: _ChatHost) -> None:
        self._chat = chat
        self.writers: list[ChatOutputWriter] = []
        self.callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._writers_with_callbacks: set[int] = set()
        self.closed = False

    def _build_formatted_callback(
        self, writer: ChatOutputWriter
    ) -> Callable[[dict[str, Any]], None]:
        """Create a callback that formats output before writing."""

        def callback(item: dict[str, Any]) -> None:
            message_type = item.get("message_type")
            message_id = item.get("message_id")

            if (
                message_type in SUPERCHAT_DEDUP_TYPES
                and message_id
                and not self._chat._register_seen_message_id(message_id)
            ):
                return

            writer.write(self._chat.format(item), flush=True)

        return callback

    @staticmethod
    def _build_raw_callback(
        writer: ChatOutputWriter,
    ) -> Callable[[dict[str, Any]], None]:
        """Create a callback that writes raw message dictionaries."""

        def callback(item: dict[str, Any]) -> None:
            writer.write(item, flush=True)

        return callback

    def _initialise_writers(self) -> None:
        """Initialise attached writers once and install their callbacks."""
        for writer in self.writers:
            if id(writer) in self._writers_with_callbacks:
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

            callback = (
                self._build_formatted_callback(writer)
                if writer.output_mode == "formatted"
                else self._build_raw_callback(writer)
            )
            self.callbacks.append(callback)
            self._writers_with_callbacks.add(id(writer))

    def attach_writer(self, writer: ChatOutputWriter) -> None:
        """Attach a writer to the chat."""
        self.writers.append(writer)

    def emit(self, item: dict[str, Any]) -> None:
        """Write a chat item to all configured outputs."""
        if not self.writers:
            return

        self._initialise_writers()
        for callback in self.callbacks:
            callback(item)

    def close(self) -> None:
        """Close all attached writers once and log any cleanup failures."""
        if self.closed:
            return

        self.closed = True
        for writer in self.writers:
            try:
                writer.close()
            except (OSError, RuntimeError, csv.Error) as error:
                log(
                    "warning",
                    "Suppressed close() error while finalizing output writer "
                    f"for {self._chat.title!r}: {error}",
                )

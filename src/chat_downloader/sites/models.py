# SPDX-License-Identifier: MIT

"""Shared site model types."""

from __future__ import annotations

import csv
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log
from chat_downloader.utils.console_utils import (
    safe_print,
    sanitize_filename_component,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

    from chat_downloader.sites.base import BaseChatDownloader

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


@dataclass(slots=True)
class Image:
    """An image with URL, optional dimensions, and an auto-generated ID."""

    url: str
    width: int | None = None
    height: int | None = None
    image_id: str | None = field(default=None, repr=False)
    id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Normalize protocol-relative URLs and derive a stable image ID."""
        if self.url.startswith("//"):
            self.url = "https:" + self.url

        if self.width and self.height and not self.image_id:
            self.id = f"{self.width}x{self.height}"
        elif self.image_id:
            self.id = self.image_id

    def json(self) -> dict[str, Any]:
        """Return the JSON representation of an Image."""
        return {
            k: v
            for k in ("url", "width", "height", "id")
            if (v := getattr(self, k)) is not None
        }


@dataclass(frozen=True)
class SiteDefault:
    """Marker object used to ask a site for its default value."""

    name: str


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

    def write(self, item: dict[str, Any] | str, flush: bool = False) -> None:
        """Write a chat item to the output target."""
        ...

    def close(self) -> None:
        """Close the writer and release any held resources."""
        ...


class _SeenMessageCache:
    """Track recently seen message IDs with bounded FIFO eviction."""

    def __init__(self, limit: int = DEFAULT_MAX_SEEN_MESSAGE_IDS) -> None:
        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError):
            log(
                "warning",
                f"_SeenMessageCache: ignoring invalid limit {limit!r}; "
                f"falling back to default {DEFAULT_MAX_SEEN_MESSAGE_IDS}.",
            )
            normalized_limit = DEFAULT_MAX_SEEN_MESSAGE_IDS
        else:
            if normalized_limit < 0:
                log(
                    "warning",
                    f"_SeenMessageCache: ignoring invalid limit {limit!r}; "
                    f"falling back to default {DEFAULT_MAX_SEEN_MESSAGE_IDS}.",
                )
                normalized_limit = DEFAULT_MAX_SEEN_MESSAGE_IDS
            elif normalized_limit == 0:
                normalized_limit = DEFAULT_MAX_SEEN_MESSAGE_IDS
        self.limit = normalized_limit
        self.message_ids: OrderedDict[str, None] = OrderedDict()
        self.evictions = 0

    def __repr__(self) -> str:
        return (
            f"_SeenMessageCache(limit={self.limit}, "
            f"size={len(self.message_ids)}, evictions={self.evictions})"
        )

    def register(self, message_id: str) -> tuple[bool, str | None]:
        """Register a message id and report whether it was new."""
        if message_id in self.message_ids:
            return False, None

        self.message_ids[message_id] = None
        self.message_ids.move_to_end(message_id)

        evicted_message_id: str | None = None
        if len(self.message_ids) > self.limit:
            evicted_message_id, _ = self.message_ids.popitem(last=False)
            self.evictions += 1

        return True, evicted_message_id


class _ChatOutputDispatcher:
    """Manage writer setup, callback dispatch, and shutdown for a chat."""

    def __init__(self, chat: Chat) -> None:
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
                safe_title = sanitize_filename_component(
                    self._chat.title
                ).replace("..", "_")
                safe_id = sanitize_filename_component(self._chat.id).replace(
                    "..", "_"
                )
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


class Chat:
    """Manage chat data for a single stream or video."""

    def __init__(
        self,
        chat: Generator[Any, Any, Any] | Iterator[Any] | None = None,
        title: str | None = None,
        duration: float | None = None,
        status: str | None = None,
        video_type: str | None = None,
        start_time: float | None = None,
        id: str | None = None,
        max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
        **kwargs: Any,
    ) -> None:
        """Set up chat metadata, output dispatch, and deduplication state."""
        self.chat = chat

        self.title = title
        self.duration = duration

        self.status = status
        self.video_type = video_type

        self.start_time = start_time
        self.id = id

        # Site object that produced this chat — set by configure_chat().
        self.site: BaseChatDownloader | None = None

        # Formatter used by print_formatted() and writer callbacks. The default
        # keeps Chat usable without pipeline configuration.
        self._formatter: Callable[[dict[str, Any]], str] = lambda item: str(
            item
        )

        self._output_dispatcher = _ChatOutputDispatcher(self)

        # Track message IDs for deduplication (YouTube superchat/ticker items)
        max_seen_message_ids = (
            int(max_seen_message_ids)
            if max_seen_message_ids is not None and max_seen_message_ids > 0
            else 0
        )
        self._seen_message_cache = _SeenMessageCache(max_seen_message_ids)

    def _register_seen_message_id(self, message_id: str) -> bool:
        added, evicted_message_id = self._seen_message_cache.register(
            message_id
        )

        if not added:
            return False

        if evicted_message_id is not None:
            log(
                "debug",
                f"Dedup cache limit reached "
                f"({self._seen_message_cache.limit}); "
                f"evicting message_id={evicted_message_id!r}.",
            )
        return True

    def set_formatter(self, formatter: Callable[[dict[str, Any]], str]) -> None:
        """Set the callable used to render formatted chat items."""
        self._formatter = formatter

    def format(self, item: dict[str, Any]) -> str:
        """Render one chat item with the currently configured formatter."""
        return self._formatter(item)

    def __iter__(self) -> Chat:
        """Allow the chat object to be iterable."""
        return self

    def attach_writer(self, writer: ChatOutputWriter) -> None:
        """Attach a writer to this chat."""
        self._output_dispatcher.attach_writer(writer)

    def close(self) -> None:
        """Close all attached writers once."""
        self._output_dispatcher.close()

    def __next__(self) -> dict[str, Any]:
        """Get the next chat message from the generator."""
        if self.chat is None:
            msg = "No chat generator available"
            raise StopIteration(msg)

        try:
            item: dict[str, Any] = next(self.chat)
            self._output_dispatcher.emit(item)
            return item
        except BaseException:
            # Close writers while unwinding any generator termination,
            # including KeyboardInterrupt/SystemExit. Runner-level cleanup may
            # call close() again; the dispatcher keeps that idempotent.
            try:
                self.close()
            except Exception as close_error:
                # Preserve the original iteration error.
                log(
                    "debug",
                    "Suppressed close() error while unwinding chat: "
                    f"{close_error}",
                )
            raise

    def print_formatted(self, item: dict[str, Any], flush: bool = True) -> None:
        """Safely print the formatted message."""
        safe_print(self.format(item), flush=flush)

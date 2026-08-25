# SPDX-License-Identifier: MIT

"""Shared site model types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log
from chat_downloader.models import SiteDefault
from chat_downloader.utils.console_utils import safe_print

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.output_dispatch import (
        ChatOutputWriter,
        _ChatOutputDispatcher,
    )


__all__ = ["Chat", "Image", "SiteDefault"]


def _default_formatter(item: dict[str, object]) -> str:
    """Render a chat item before a pipeline formatter is configured."""
    return str(item)


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
        id: str | None = None,  # noqa: A002 — public Chat API; renaming `id` would break callers
        max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
        diagnostics: dict[str, object] | None = None,
        **kwargs: Any,  # noqa: ARG002 — forward-compat absorber; site subclasses pass extra fields
    ) -> None:
        """Set up chat metadata, output dispatch, and deduplication state."""
        self.chat = chat

        self.title = title
        self.duration = duration

        self.status = status
        self.video_type = video_type

        self.start_time = start_time
        self.id = id
        self.diagnostics = diagnostics if diagnostics is not None else {}

        # Site object that produced this chat — set by configure_chat().
        self.site: BaseChatDownloader | None = None

        # Formatter used by print_formatted() and writer callbacks. The default
        # keeps Chat usable without pipeline configuration.
        self._formatter: Callable[[dict[str, Any]], str] = _default_formatter

        # Output dispatch (writers, deduplication, and shutdown) is created
        # lazily when the first writer is attached, so this model module stays
        # free of the output layer at import time. The deduplication-cache size
        # is remembered here and handed to the dispatcher, which owns it.
        self._output_dispatcher: _ChatOutputDispatcher | None = None
        self._generator_closed = False
        self._max_seen_message_ids = max_seen_message_ids

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
        """Attach a writer to this chat, creating the dispatcher on demand."""
        if self._output_dispatcher is None:
            # Deferred import: output dispatch is only needed once a caller
            # actually wires up output, keeping sites.models import-light.
            from chat_downloader.sites.output_dispatch import _ChatOutputDispatcher

            self._output_dispatcher = _ChatOutputDispatcher(
                self, self._max_seen_message_ids
            )
        self._output_dispatcher.attach_writer(writer)

    def close(self) -> None:
        """Close the message source and all attached writers once."""
        try:
            if not self._generator_closed:
                self._generator_closed = True
                close_generator = getattr(self.chat, "close", None)
                if callable(close_generator):
                    try:
                        close_generator()
                    except (OSError, RuntimeError, ValueError) as error:
                        log(
                            "debug",
                            f"Suppressed chat generator close() error: {error}",
                        )
        finally:
            if self._output_dispatcher is not None:
                self._output_dispatcher.close()

    @property
    def write_error_count(self) -> int:
        """Return the number of writer close errors encountered."""
        if self._output_dispatcher is None:
            return 0
        return self._output_dispatcher.write_error_count

    def __next__(self) -> dict[str, Any]:
        """Get the next chat message from the generator."""
        if self.chat is None:
            msg = "No chat generator available"
            raise StopIteration(msg)

        try:
            item: dict[str, Any] = next(self.chat)
            if self._output_dispatcher is not None:
                self._output_dispatcher.emit(item)
        except BaseException:
            # Close writers while unwinding any generator termination,
            # including KeyboardInterrupt/SystemExit. Runner-level cleanup may
            # call close() again; the dispatcher keeps that idempotent.
            try:
                self.close()
            except Exception as close_error:  # noqa: BLE001 — close() must not mask the iteration error being unwound
                # Preserve the original iteration error.
                log(
                    "debug",
                    f"Suppressed close() error while unwinding chat: {close_error}",
                )
            raise
        else:
            return item

    def print_formatted(self, item: dict[str, Any], *, flush: bool = True) -> None:
        """Safely print the formatted message."""
        safe_print(self.format(item), flush=flush)

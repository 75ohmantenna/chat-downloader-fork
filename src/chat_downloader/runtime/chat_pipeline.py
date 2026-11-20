# SPDX-License-Identifier: MIT

"""Chat pipeline configuration helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.output_dispatch import configure_outputs
from chat_downloader.utils.time_utils import ensure_seconds
from chat_downloader.utils.timed_generator import TimedGenerator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest, SiteDefault
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat


class _MessageLimitIterator[Message]:
    """Limit an iterator while preserving its explicit close lifecycle."""

    def __init__(
        self,
        source: Iterator[Message],
        limit: int,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        self._source = source
        self._remaining = limit
        self._diagnostics = diagnostics
        self._closed = False

    def __iter__(self) -> _MessageLimitIterator[Message]:
        return self

    def __next__(self) -> Message:
        if self._closed or self._remaining <= 0:
            self.close()
            raise StopIteration
        try:
            item = next(self._source)
        except BaseException:
            self.close()
            raise
        self._remaining -= 1
        if self._remaining == 0 and self._diagnostics is not None:
            self._diagnostics["termination_reason"] = "message_limit"
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._source, "close", None)
        if callable(close):
            close()

    def request_stop(self) -> None:
        """Preserve cooperative cancellation through the message-limit wrapper."""
        request_stop = getattr(self._source, "request_stop", None)
        if callable(request_stop):
            request_stop()


def _apply_message_limit(chat: Chat, max_messages: int | None) -> None:
    """Apply maximum message limit to a chat generator."""
    if max_messages is not None and chat.chat is not None:
        chat.chat = _MessageLimitIterator(chat.chat, max_messages, chat.diagnostics)


def _configure_timeouts(
    chat: Chat, timeout: float | None, inactivity_timeout: float | None
) -> None:
    """Configure timeout and inactivity timeout on a chat generator."""
    if timeout is None and inactivity_timeout is None:
        return
    if chat.chat is None:
        return

    chat.chat = TimedGenerator(chat.chat, timeout, inactivity_timeout)

    if isinstance(timeout, (float, int)):
        start_time = time.monotonic()

        def log_on_timeout() -> None:
            chat.diagnostics["termination_reason"] = "timeout"
            elapsed = time.monotonic() - start_time
            log("debug", f"Timeout occurred after {elapsed} seconds.")

        chat.chat.on_timeout = log_on_timeout

    if isinstance(inactivity_timeout, (float, int)):

        def log_on_inactivity_timeout() -> None:
            chat.diagnostics["termination_reason"] = "inactivity_timeout"
            log(
                "debug",
                f"Inactivity timeout occurred after {inactivity_timeout} seconds.",
            )

        chat.chat.on_inactivity_timeout = log_on_inactivity_timeout


def _resolve_format_name(
    chat: Chat, format_name: str | SiteDefault
) -> str | SiteDefault:
    """Select a live-aware format override via the site's own capability."""
    if not isinstance(format_name, str):
        return format_name

    site: BaseChatDownloader | None = getattr(chat, "site", None)
    if site is None:
        return format_name

    if site.is_live_status(getattr(chat, "status", None)):
        return site.resolve_live_format(format_name)

    return format_name


def _configure_formatter(
    chat: Chat, format_file: str | None, format_name: str | SiteDefault
) -> None:
    """Configure message formatting for chat output."""
    formatter = ItemFormatter(format_file)

    def selected_name() -> str:
        # Channel discovery can establish live status after configuration.
        selected = _resolve_format_name(chat, format_name)
        if isinstance(selected, str):
            return selected
        if selected.name == "format":
            return ItemFormatter.DEFAULT_FORMAT_NAME
        return selected.name

    formatter.validate_format_name(selected_name())

    def format_callable(item: dict[str, Any]) -> str:
        return formatter.format(item, format_name=selected_name())

    chat.set_formatter(format_callable)


def configure_chat(
    chat: Chat, request: ChatRequest, site_object: BaseChatDownloader
) -> None:
    """Configure limits, timeouts, formatting, and output for a chat."""
    chat.site = site_object
    if chat._request_window is None:
        chat._request_window = {
            "start_time": ensure_seconds(request.start_time),
            "end_time": ensure_seconds(request.end_time),
            "time_basis": "clip" if chat.video_type == "clip" else "recording",
        }
    _configure_formatter(chat, request.format_file, request.format)
    configure_outputs(
        chat, request.output, overwrite=request.overwrite, sort_keys=request.sort_keys
    )
    # Start workers only after configuration succeeds; limit precedes timeout.
    _apply_message_limit(chat, request.max_messages)
    _configure_timeouts(chat, request.timeout, request.inactivity_timeout)

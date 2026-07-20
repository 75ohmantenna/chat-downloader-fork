# SPDX-License-Identifier: MIT

"""Chat pipeline configuration helpers."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Protocol

from chat_downloader.debugging import log
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.utils.timed_generator import TimedGenerator

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest, SiteDefault
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat


class _MessageSource(Protocol):
    def __next__(self) -> object: ...


class _MessageLimitIterator:
    """Limit an iterator while preserving its explicit close lifecycle."""

    def __init__(self, source: _MessageSource, limit: int) -> None:
        self._source = source
        self._remaining = limit
        self._closed = False

    def __iter__(self) -> _MessageLimitIterator:
        return self

    def __next__(self) -> object:
        if self._closed or self._remaining <= 0:
            self.close()
            raise StopIteration
        try:
            item = next(self._source)
        except BaseException:
            self.close()
            raise
        self._remaining -= 1
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._source, "close", None)
        if callable(close):
            close()


def _apply_message_limit(chat: Chat, max_messages: int | None) -> None:
    """Apply maximum message limit to a chat generator."""
    if max_messages is not None and chat.chat is not None:
        chat.chat = _MessageLimitIterator(chat.chat, max_messages)


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
            elapsed = time.monotonic() - start_time
            log("debug", f"Timeout occurred after {elapsed} seconds.")

        chat.chat.on_timeout = log_on_timeout

    if isinstance(inactivity_timeout, (float, int)):

        def log_on_inactivity_timeout() -> None:
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
    raw = _resolve_format_name(chat, format_name)
    # By the time configure_formatter is called, format_name should have been
    # resolved to a str via resolved_for_site().  SiteDefault.name is the
    # fallback for any unresolved placeholders.
    format_str = raw if isinstance(raw, str) else raw.name

    def format_callable(item: dict[str, Any]) -> str:
        return formatter.format(item, format_name=format_str)

    chat.set_formatter(format_callable)


def _build_output_writer(
    output_file: str,
    request: ChatRequest,
    writer_factory: Any = ContinuousWriter,
) -> Any:
    """Create an output writer from a request's writer-relevant settings."""
    return writer_factory(
        output_file,
        sort_keys=request.sort_keys,
        overwrite=request.overwrite,
        lazy_initialise=True,
    )


def _configure_output_writer(
    chat: Chat,
    request: ChatRequest,
    writer_factory: Any = ContinuousWriter,
) -> None:
    """Attach one or more output writers to a chat object."""
    if not request.output:
        return

    outputs = request.output if isinstance(request.output, list) else [request.output]
    seen: set[str] = set()
    for output_file in outputs:
        canonical_path = os.path.normcase(os.path.realpath(output_file))
        if canonical_path in seen:
            log(
                "warning",
                f"Duplicate output path '{output_file}' — skipping.",
            )
            continue
        seen.add(canonical_path)
        chat.attach_writer(_build_output_writer(output_file, request, writer_factory))


def configure_chat(
    chat: Chat, request: ChatRequest, site_object: BaseChatDownloader
) -> None:
    """Configure limits, timeouts, formatting, and output for a chat."""
    chat.site = site_object
    # Mutates chat.chat in wrapper order: limit first, then timeout wrapper.
    _apply_message_limit(chat, request.max_messages)
    _configure_timeouts(chat, request.timeout, request.inactivity_timeout)
    _configure_formatter(chat, request.format_file, request.format)
    _configure_output_writer(chat, request)

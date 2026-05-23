# SPDX-License-Identifier: MIT

"""Chat pipeline configuration helpers."""

from __future__ import annotations

import itertools
import os
import time
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.youtube.video_status_models import REPLAY_STATUSES
from chat_downloader.utils.timed_utils import TimedGenerator

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat

# Statuses that indicate an ongoing or recently-ended live broadcast.
# post_live = DVR available but stream not yet fully processed.
LIVE_STATUSES: frozenset[str] = frozenset({"live", "post_live"})

# Re-export so callers can import REPLAY_STATUSES from either location.
__all__ = ["LIVE_STATUSES", "REPLAY_STATUSES"]

_YOUTUBE_LIVE_FORMAT_OVERRIDES: dict[str, str] = {
    "default": "youtube_live_default",
    "youtube": "youtube_live_default",
    "24_hour": "youtube_live_24_hour",
    "12_hour": "youtube_live_12_hour",
}


def apply_message_limit(chat: Chat, max_messages: int | None) -> None:
    """Apply maximum message limit to a chat generator."""
    if max_messages is not None and chat.chat is not None:
        chat.chat = itertools.islice(chat.chat, max_messages)


def configure_timeouts(
    chat: Chat, timeout: Any, inactivity_timeout: Any
) -> None:
    """Configure timeout and inactivity timeout on a chat generator."""
    if timeout is None and inactivity_timeout is None:
        return
    if chat.chat is None:
        return

    chat.chat = TimedGenerator(chat.chat, timeout, inactivity_timeout)

    if isinstance(timeout, (float, int)):
        start_time = time.time()

        def log_on_timeout() -> None:
            elapsed = time.time() - start_time
            log("debug", f"Timeout occurred after {elapsed} seconds.")

        chat.chat.on_timeout = log_on_timeout

    if isinstance(inactivity_timeout, (float, int)):

        def log_on_inactivity_timeout() -> None:
            log(
                "debug",
                "Inactivity timeout occurred after "
                f"{inactivity_timeout} seconds.",
            )

        chat.chat.on_inactivity_timeout = log_on_inactivity_timeout


def _is_youtube_chat(chat: Chat) -> bool:
    """Return True when the chat was produced by the YouTube downloader."""
    site = getattr(chat, "site", None)
    return getattr(site, "_NAME", None) == "youtube.com"


def _resolve_format_name(chat: Chat, format_name: Any) -> Any:
    """Select a live-aware format override when YouTube provides both time
    fields.
    """
    if not isinstance(format_name, str):
        return format_name

    if _is_youtube_chat(chat) and is_live_stream(chat):
        return _YOUTUBE_LIVE_FORMAT_OVERRIDES.get(format_name, format_name)

    return format_name


def configure_formatter(chat: Chat, format_file: Any, format_name: Any) -> None:
    """Configure message formatting for chat output."""
    formatter = ItemFormatter(format_file)
    resolved_format_name = _resolve_format_name(chat, format_name)

    def format_callable(item: dict[str, Any]) -> str:
        return formatter.format(
            item,
            format_name=resolved_format_name,
        )

    chat.set_formatter(format_callable)


def is_live_stream(chat: Chat) -> bool:
    """Return True when chat.status indicates an active/recent live stream."""
    return getattr(chat, "status", None) in LIVE_STATUSES


def maybe_upgrade_to_jsonl(
    output_file: str,
    is_live: bool,
    output_format: str | None = None,
) -> str:
    """Upgrade a ``.json`` output path to ``.jsonl`` for live captures."""
    if not is_live:
        return output_file
    if output_format == "json":
        return output_file

    stem, ext = os.path.splitext(output_file)
    if ext.lower() == ".json":
        new_file = stem + ".jsonl"
        log(
            "warning",
            f"Live stream detected: switching output from '{output_file}' "
            f"to '{new_file}' (JSONL is crash-safe for live captures). "
            f"Use --format json to keep JSON array output.",
        )
        return new_file
    return output_file


def build_output_writer(
    output_file: str,
    request: ChatRequest,
    writer_factory: Any = ContinuousWriter,
) -> Any:
    """Create an output writer from a request's writer-relevant settings."""
    return writer_factory(
        output_file,
        indent=request.indent,
        sort_keys=request.sort_keys,
        overwrite=request.overwrite,
        lazy_initialise=True,
    )


def configure_output_writer(
    chat: Chat,
    request: ChatRequest,
    writer_factory: Any = ContinuousWriter,
) -> None:
    """Attach one or more output writers to a chat object."""
    if not request.output:
        return

    outputs = (
        request.output if isinstance(request.output, list) else [request.output]
    )
    live = is_live_stream(chat)

    seen: set[str] = set()
    for output_file in outputs:
        fmt = request.format if isinstance(request.format, str) else None
        output_file = maybe_upgrade_to_jsonl(output_file, live, fmt)
        if output_file in seen:
            log(
                "warning",
                "Duplicate output path after live promotion: "
                f"'{output_file}' — skipping.",
            )
            continue
        seen.add(output_file)
        chat.attach_writer(
            build_output_writer(output_file, request, writer_factory)
        )


def configure_chat(
    chat: Chat, request: ChatRequest, site_object: BaseChatDownloader
) -> None:
    """Configure limits, timeouts, formatting, and output for a chat."""
    chat.site = site_object
    # Mutates chat.chat in wrapper order: limit first, then timeout wrapper.
    apply_message_limit(chat, request.max_messages)
    configure_timeouts(chat, request.timeout, request.inactivity_timeout)
    configure_formatter(chat, request.format_file, request.format)
    configure_output_writer(chat, request)

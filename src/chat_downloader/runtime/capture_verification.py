# SPDX-License-Identifier: MIT

"""Post-shutdown parity checks using the capture's resolved formatter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import ChatDownloaderError
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.sites.output_dispatch import _expand_output_file_name

if TYPE_CHECKING:
    from chat_downloader.sites.models import Chat
    from chat_downloader.utils.json_types import JSONDict


class _ChatFormatter:
    def __init__(self, chat: Chat) -> None:
        self.chat = chat

    def format(self, item: JSONDict, format_name: str) -> str:  # noqa: ARG002 — audit protocol
        return self.chat.format(item)


def capture_paths(chat: Chat) -> dict[str, Path]:
    """Resolve a distinct pair before output starts and after it closes."""
    dispatcher = chat._output_dispatcher
    if dispatcher is None:
        msg = "Output verification requires one JSONL and one TXT file."
        raise ValueError(msg)
    paths = [
        Path(
            item["file_name"]
            if item["file_created"]
            else _expand_output_file_name(
                item["file_name"], title=chat.title, video_id=chat.id
            )
        )
        for item in dispatcher.writer_summaries
    ]
    by_suffix = {path.suffix.lower(): path for path in paths}
    if len(paths) != 2 or set(by_suffix) != {".jsonl", ".txt"}:
        msg = "Output verification requires one JSONL and one TXT file."
        raise ValueError(msg)
    first, second = paths
    if first.resolve() == second.resolve() or (
        first.exists() and second.exists() and first.samefile(second)
    ):
        msg = "Output verification requires distinct files."
        raise ValueError(msg)
    return by_suffix


def verify_capture(
    chat: Chat, *, resets: tuple[int, ...] = (), allow_existing: bool = False
) -> None:
    """Audit the closed pair, including append-run deduplication boundaries."""
    by_suffix = capture_paths(chat)
    dispatcher = chat._output_dispatcher
    if (
        not allow_existing
        and dispatcher is not None
        and any(
            not item["file_created"]
            and by_suffix[Path(item["file_name"]).suffix.lower()].exists()
            for item in dispatcher.writer_summaries
        )
    ):
        msg = "Existing output artifacts were not produced by this run."
        raise ChatDownloaderError(msg)
    stats = audit_capture(
        by_suffix[".jsonl"],
        by_suffix[".txt"],
        formatter=_ChatFormatter(chat),
        format_name="resolved",
        dedup_reset_before_lines=resets,
        max_seen_message_ids=chat._max_seen_message_ids,
    )
    if stats.failed or not stats.comparison_complete:
        msg = "JSONL/TXT output parity verification failed."
        raise ChatDownloaderError(msg)


def validate_verification(parameters: dict[str, Any], *, resume: bool) -> None:
    """Reject invalid pairs before lazy outputs can open or truncate anything."""
    outputs = parameters.get("output")
    if (
        not isinstance(outputs, list)
        or len(outputs) != 2
        or {Path(name).suffix.lower() for name in outputs} != {".jsonl", ".txt"}
    ):
        msg = "Output verification requires one JSONL and one TXT file."
        raise ValueError(msg)
    if parameters.get("overwrite") is False and not resume:
        msg = "Append verification requires a resume checkpoint."
        raise ValueError(msg)


def inspect_provider_capture(chat: Chat) -> dict[str, object] | None:
    """Run the provider's optional closed-artifact inspector without log contents."""
    if chat._capture_inspector is None:
        return None
    try:
        path = capture_paths(chat)[".jsonl"]
        dispatcher = chat._output_dispatcher
        created = dispatcher is not None and any(
            item["file_created"] and Path(item["file_name"]).suffix.lower() == ".jsonl"
            for item in dispatcher.writer_summaries
        )
        report = chat._capture_inspector(path if created else None)
        deadline_summary = getattr(chat.chat, "deadline_prefetch_summary", None)
        count, complete = (
            deadline_summary() if callable(deadline_summary) else (0, True)
        )
        report["prefetched_after_deadline_count"] = count
        report["deadline_prefetch_count_complete"] = complete
    except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError):
        return {"status": "error", "error": "provider_inspection_failed"}
    else:
        return report

# SPDX-License-Identifier: MIT

"""Exclusive, content-free run reports and replay completeness policy."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.metadata import __version__
from chat_downloader.redaction import sanitize_for_log
from chat_downloader.sites.output_dispatch import _expand_output_file_name

if TYPE_CHECKING:
    from collections.abc import Mapping

    from chat_downloader.sites.models import Chat

    from .runner import RunResult


def replay_complete(chat: Chat | None, result: RunResult) -> bool:
    """Require successful exhaustion, excluding known provider parsing loss."""
    if chat is None or chat.status != "completed" or not result.success:
        return False
    state = chat.diagnostics
    return (
        result.termination_reason in {"completed", "empty_window"}
        and state.get("history_complete", True) is True
        and not has_record_loss(state)
    )


def has_record_loss(state: Mapping[str, object]) -> bool:
    """Keep known loss sticky across checkpoints and completion reports."""
    return any(
        state.get(key, 0)
        for key in (
            "malformed_timestamp",
            "malformed_object",
            "parse_error",
            "prior_record_loss",
        )
    )


class RunManifest:
    """Validate artifact separation before capture; never replace an existing file."""

    def __init__(self, name: str, resume: str | None) -> None:
        """Reject existing destinations and reserved checkpoint paths."""
        self.path = Path(name).absolute()
        if self.path.exists() or self.path.is_symlink():
            msg = "Run manifest requires a new file."
            raise ValueError(msg)
        if resume and self.path.resolve() in {
            Path(resume).resolve(),
            Path(resume + ".lock").resolve(),
        }:
            msg = "Run manifest must be distinct from the checkpoint."
            raise ValueError(msg)
        self.outputs: list[Path] = []
        self.valid = True

    def bind(self, chat: Chat) -> None:
        """Resolve lazy names before any output writer can open its file."""
        self.valid = False
        dispatcher = chat._output_dispatcher
        self.outputs = (
            [
                Path(
                    _expand_output_file_name(
                        item["file_name"], title=chat.title, video_id=chat.id
                    )
                )
                for item in dispatcher.writer_summaries
            ]
            if dispatcher
            else []
        )
        if any(path.resolve() == self.path.resolve() for path in self.outputs):
            msg = "Run manifest must be distinct from chat outputs."
            raise ValueError(msg)

        self.valid = True

    def write(
        self, chat: Chat | None, result: RunResult, *, verified_existing: bool = False
    ) -> None:
        """Write final outcome and full artifact hashes after writers close."""
        if not self.valid:
            msg = "Run manifest output paths were not validated."
            raise ValueError(msg)
        dispatcher = getattr(chat, "_output_dispatcher", None)
        writers = dispatcher.writer_summaries if dispatcher else []
        artifacts: list[dict[str, object]] = []
        for path, writer in zip(self.outputs, writers, strict=True):
            signature = None
            # Unopened files can predate this run: never certify them as output.
            if writer["file_created"] or (verified_existing and path.exists()):
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        msg = "Manifest artifacts must be regular files."
                        raise ValueError(msg)
                    signature = hashlib.file_digest(source, "sha256").hexdigest()
            artifacts.append({**writer, "sha256": signature})
        payload = {
            "schema_version": 1,
            "program_version": __version__,
            "success": result.success,
            "replay_complete": replay_complete(chat, result),
            "termination_reason": result.termination_reason,
            "parity_status": result.parity_status,
            "message_count": result.message_count,
            "prior_message_count": getattr(chat, "diagnostics", {}).get(
                "prior_message_count", 0
            ),
            "message_type_counts": result.message_type_counts,
            "recording": {
                key: getattr(chat, key, None)
                for key in (
                    "site_name",
                    "id",
                    "start_time",
                    "duration",
                    "status",
                )
            },
            "outputs": artifacts,
        }
        encoded = json.dumps(sanitize_for_log(payload), allow_nan=False, indent=2)
        # Exclusive creation protects files created after preflight as well.
        with self.path.open("x", encoding="utf-8") as target:
            target.write(encoded)
            target.write("\n")


def validate_complete_request(chat: Chat) -> None:
    """Reject live and unknown replay states before lazy output opens."""
    if chat.status != "completed":
        msg = "Complete capture requires a completed replay."
        raise ValueError(msg)

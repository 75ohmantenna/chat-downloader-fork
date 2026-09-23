# SPDX-License-Identifier: MIT

"""Validated shutdown checkpoints for append-only replay captures."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from chat_downloader.errors import ChatDownloaderError
from chat_downloader.models import ChatRequest

from .capture_manifest import has_record_loss, is_completed_replay

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from chat_downloader.sites.models import Chat
    from chat_downloader.utils.json_types import JSONDict

_BOUNDARY_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class _RecordBoundary:
    """File and in-memory state before one checkpointed emission."""

    sizes: tuple[int | None, ...]
    writer_counts: dict[int, int]
    formatted_duplicates: int
    offset: float | None
    ids: frozenset[str]


def _valid_offset(value: object) -> TypeGuard[int | float]:
    return (type(value) is int or type(value) is float) and math.isfinite(value)


def _file_signature(path: Path) -> str | None:
    """Hash a regular artifact, distinguishing lazy absence from bad inputs."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        msg = "Checkpoint artifacts must be regular files, not links."
        raise ValueError(msg)
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def _atomic_json(path: Path, data: Mapping[str, object]) -> None:
    """Commit a checkpoint only after its capture writers have closed."""
    descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


@contextmanager
def checkpoint_lock(name: str) -> Generator[None, None, None]:
    """Exclude concurrent writers; stale locks require explicit operator recovery."""
    lock = Path(name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink()


class _CheckpointSource:
    """Retain source cleanup and deadline accounting around overlap filtering."""

    def __init__(
        self, source: object, remaining: Generator[JSONDict, None, None]
    ) -> None:
        self.source, self.remaining = source, remaining
        self.started = False
        self.closed = False

    def __iter__(self) -> _CheckpointSource:
        return self

    def __next__(self) -> JSONDict:
        self.started = True
        return next(self.remaining)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.remaining.close()
        if not self.started:
            close = getattr(self.source, "close", None)
            if callable(close):
                close()

    def deadline_prefetch_summary(self) -> tuple[int, bool]:
        summary = getattr(self.source, "deadline_prefetch_summary", None)
        return cast("tuple[int, bool]", summary()) if callable(summary) else (0, True)


class CaptureCheckpoint:
    """Own request identity, overlap suppression, and verified output state.

    Checkpoints advance on normal or interrupted shutdown, after writer fsync.
    A crash after an earlier checkpoint leaves a detectable artifact mismatch;
    resumption never truncates or guesses how to repair those artifacts.
    """

    def __init__(self, name: str, parameters: dict[str, Any]) -> None:
        """Validate checkpoint identity before any output can be opened."""
        self.path = Path(name).absolute()
        _file_signature(self.path)
        request = ChatRequest.from_kwargs(strict=True, **parameters)
        outputs = request.output
        names = outputs if isinstance(outputs, list) else [outputs] if outputs else []
        if not names or any("{" in name or "}" in name for name in names):
            msg = "Resume requires explicit output paths without placeholders."
            raise ValueError(msg)
        self.outputs = [Path(name).absolute() for name in names]
        identities = [path.resolve() for path in [self.path, *self.outputs]]
        if len(set(identities)) != len(identities):
            msg = "Checkpoint and output paths must be distinct."
            raise ValueError(msg)
        if len({path.suffix.lower() for path in self.outputs}) != len(self.outputs):
            msg = "Resume accepts at most one JSONL and one TXT output."
            raise ValueError(msg)
        # Immutable request semantics are hashed; credentials never enter state.
        identity = request.as_dict()
        for key in (
            "max_messages",
            "timeout",
            "inactivity_timeout",
            "overwrite",
            "max_attempts",
            "retry_timeout",
            "interruptible_retry",
        ):
            identity.pop(key, None)
        identity["output"] = [str(path) for path in self.outputs]
        identity["format_file"] = (
            _file_signature(Path(request.format_file)) if request.format_file else None
        )
        self.fingerprint = hashlib.sha256(
            json.dumps(identity, default=str, sort_keys=True).encode()
        ).hexdigest()
        self.offset: float | None = None
        self.ids: set[str] = set()
        self.total = 0
        self.resets: list[int] = []
        self.chat_id: str | None = None
        self.completed = False
        self.loaded = False
        self.record_loss = False
        self.limit = request.max_messages
        self._prior_offset: float | None = None
        self._prior_ids: set[str] = set()
        self._initial_signatures = [_file_signature(path) for path in self.outputs]
        if self.path.exists():
            self._load()
        elif any(value is not None for value in self._initial_signatures):
            msg = "A new checkpoint requires absent output files."
            raise ValueError(msg)
        self._prior_offset, self._prior_ids = self.offset, set(self.ids)
        parameters["overwrite"] = False
        parameters["max_messages"] = None  # Count after overlap suppression.
        resume_offset = cast("float | None", self.offset)
        if resume_offset is not None:
            parameters["start_time"] = math.floor(resume_offset) - 1

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as stream:
            state = json.load(stream)
        if (
            not isinstance(state, dict)
            or state.get("version") != 1
            or state.get("request") != self.fingerprint
        ):
            msg = "Checkpoint does not match this replay request."
            raise ValueError(msg)
        if state.get("artifacts") != self._initial_signatures:
            msg = "Capture files changed since the checkpoint; refusing to append."
            raise ValueError(msg)
        self.record_loss = state.get("record_loss", False)
        offset, ids, total = state.get("offset"), state.get("ids"), state.get("total")
        if (
            (offset is not None and not _valid_offset(offset))
            or type(self.record_loss) is not bool
            or not isinstance(ids, list)
            or len(ids) > _BOUNDARY_LIMIT
            or any(not isinstance(item, str) for item in ids)
            or type(total) is not int
            or total < 0
        ):
            msg = "Invalid checkpoint boundary."
            raise ValueError(msg)
        self.offset, self.ids, self.total = offset, set(ids), total
        self.chat_id = state.get("chat_id")
        self.resets = state.get("resets", [])
        if (
            not isinstance(self.chat_id, str)
            or not isinstance(self.resets, list)
            or any(
                type(item) is not int or item < 1 or item > total
                for item in self.resets
            )
            or sorted(set(self.resets)) != self.resets
        ):
            msg = "Invalid checkpoint identity or output boundaries."
            raise ValueError(msg)
        self.loaded = True

    def bind(self, chat: Chat) -> None:  # noqa: C901 — replay boundary validation and source lifecycle
        """Validate metadata and filter already committed overlap before output."""
        if not is_completed_replay(chat) or not chat.id or chat.chat is None:
            msg = "Resume requires a completed replay with stable identity."
            raise ValueError(msg)
        if self.chat_id is not None and chat.id != self.chat_id:
            msg = "Checkpoint video identity changed."
            raise ValueError(msg)
        self.chat_id = chat.id
        chat.diagnostics["checkpoint_overlap_suppressed"] = 0
        chat.diagnostics["prior_message_count"] = self.total
        chat.diagnostics["prior_record_loss"] = self.record_loss
        source = chat.chat

        def remaining() -> Generator[JSONDict, None, None]:
            emitted = 0
            last_offset = self._prior_offset
            try:
                for item in source:
                    # Provider-generated end markers carry no replay position
                    # and do not represent an archival chat record.
                    if (
                        item.get("message_type") == "chat_ended"
                        and item.get("action_type") == "chat_ended"
                        and "message_id" not in item
                        and "time_in_seconds" not in item
                    ):
                        continue
                    offset, message_id = (
                        item.get("time_in_seconds"),
                        item.get("message_id"),
                    )
                    if (
                        not _valid_offset(offset)
                        or not isinstance(message_id, str)
                        or not message_id
                    ):
                        msg = "Resume requires message IDs and finite replay offsets."
                        raise ValueError(msg)
                    if self._prior_offset is not None and (
                        offset < self._prior_offset
                        or (
                            offset == self._prior_offset
                            and message_id in self._prior_ids
                        )
                    ):
                        chat.diagnostics["checkpoint_overlap_suppressed"] = (
                            cast(
                                "int", chat.diagnostics["checkpoint_overlap_suppressed"]
                            )
                            + 1
                        )
                        continue
                    if last_offset is not None and offset < last_offset:
                        msg = "Replay offsets moved backwards; checkpoint not advanced."
                        raise ValueError(msg)
                    if (
                        offset == self.offset
                        and message_id not in self.ids
                        and len(self.ids) >= _BOUNDARY_LIMIT
                    ):
                        msg = "Checkpoint timestamp has too many message IDs."
                        raise ValueError(msg)
                    last_offset = offset
                    yield item
                    emitted += 1
                    if self.limit is not None and emitted >= self.limit:
                        chat.diagnostics["termination_reason"] = "message_limit"
                        return
                self.completed = (
                    chat.diagnostics.get("termination_reason", "completed")
                    == "completed"
                )
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    close()

        chat.chat = _CheckpointSource(source, remaining())

    def observe(self, item: JSONDict) -> None:
        """Advance state only after all attached writers accepted a record."""
        offset = float(cast("float", item["time_in_seconds"]))
        if offset != self.offset:
            self.offset, self.ids = offset, set()
        self.ids.add(cast("str", item["message_id"]))

    def begin_record(self, chat: Chat) -> _RecordBoundary:
        """Snapshot append positions before requesting the next record."""
        dispatcher = chat._output_dispatcher
        if dispatcher is None:
            msg = "Resume requires attached output writers."
            raise ChatDownloaderError(msg)
        sizes = tuple(
            path.stat().st_size if path.exists() else None for path in self.outputs
        )
        return _RecordBoundary(
            sizes,
            dict(dispatcher._records_written_by_writer),
            dispatcher._formatted_duplicates_suppressed,
            self.offset,
            frozenset(self.ids),
        )

    def rollback_record(self, chat: Chat, boundary: _RecordBoundary) -> None:
        """Discard a torn emission before saving an interrupted checkpoint."""
        for path, previous_size in zip(self.outputs, boundary.sizes, strict=True):
            if path.exists():
                with path.open("r+b") as stream:
                    stream.truncate(previous_size or 0)
        dispatcher = chat._output_dispatcher
        assert dispatcher is not None  # noqa: S101 — bound checkpoint retains its dispatcher
        dispatcher._records_written_by_writer = boundary.writer_counts
        dispatcher._formatted_duplicates_suppressed = boundary.formatted_duplicates
        self.offset, self.ids = boundary.offset, set(boundary.ids)

    def save(self, chat: Chat, count: int) -> None:
        """Keep partial-writer failures from becoming valid resume points."""
        dispatcher = chat._output_dispatcher
        if (
            dispatcher is None
            or chat.write_error_count
            or not dispatcher.counts_match(count)
        ):
            msg = "Checkpoint not saved: output records disagree."
            raise ChatDownloaderError(msg)
        if count and self.total:
            self.resets.append(self.total + 1)
        self.total += count
        record_loss = has_record_loss(chat.diagnostics)
        _atomic_json(
            self.path,
            {
                "version": 1,
                "request": self.fingerprint,
                "chat_id": self.chat_id,
                "offset": self.offset,
                "ids": sorted(self.ids),
                "total": self.total,
                "resets": self.resets,
                "completed": self.completed and not record_loss,
                "record_loss": record_loss,
                "artifacts": [_file_signature(path) for path in self.outputs],
            },
        )

# SPDX-License-Identifier: MIT

"""Finite internal capture budgets, independent of output message limits.

The largest recorded Kick fixture is under 46 KiB. An 8 MiB wire message and
4096 fragments/batch entries leave ample headroom for real public events.
Replay allows 512 MiB of serialized records, 100,000 pages and 10 million raw
records; cursor metadata has a separate 8 MiB ceiling and 4096-character keys.
History HTTP bodies are capped at 8 MiB decoded bytes before JSON parsing;
spool expansion is capped at 16 MiB per record and 64 MiB per page. Strings
are escaped in 2048-character slices (at most 24 KiB per escaped chunk).
Limits reject incomplete captures rather than silently discarding records.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from .errors import KickResourceLimit

if TYPE_CHECKING:
    from collections.abc import Iterator
    from threading import Event
    from typing import IO

    from chat_downloader.utils.json_types import JSONAny, JSONDict, JSONList


MAX_WEBSOCKET_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_WEBSOCKET_MESSAGE_FRAMES = 4096
MAX_CENTRIFUGO_BATCH_ENTRIES = 4096
MAX_CENTRIFUGO_QUEUED_FRAMES = 4096
MAX_REPLAY_SPOOL_BYTES = 512 * 1024 * 1024
MAX_REPLAY_PAGES = 100_000
MAX_REPLAY_RECORDS = 10_000_000
MAX_REPLAY_CURSOR_CHARS = 4096
MAX_REPLAY_CURSOR_BYTES = 8 * 1024 * 1024
MAX_HISTORY_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REPLAY_RECORD_BYTES = 16 * 1024 * 1024
MAX_REPLAY_PAGE_BYTES = 64 * 1024 * 1024
_JSON_STRING_CHARS = 2048


def check_replay_cursor(cursor: str | None) -> None:
    """Reject oversized cursors before retaining, parsing or reusing them."""
    if cursor is not None and len(cursor) > MAX_REPLAY_CURSOR_CHARS:
        msg = "Kick replay cursor resource limit exceeded; capture is incomplete."
        raise KickResourceLimit(msg)


def _string_parts(value: str, cancelled: Event | None) -> Iterator[str]:
    yield '"'
    for start in range(0, len(value), _JSON_STRING_CHARS):
        if cancelled is not None and cancelled.is_set():
            return
        # Escape only a bounded slice, never the complete provider string.
        yield json.dumps(value[start : start + _JSON_STRING_CHARS])[1:-1]
    yield '"'


def _json_parts(
    value: JSONAny, cancelled: Event | None, *, sort_keys: bool = False
) -> Iterator[str]:
    """Encode JSON with bounded string allocations and cancellation checkpoints."""
    if cancelled is not None and cancelled.is_set():
        return
    if isinstance(value, str):
        yield from _string_parts(value, cancelled)
    elif isinstance(value, list):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ", "
            yield from _json_parts(item, cancelled, sort_keys=sort_keys)
        yield "]"
    elif isinstance(value, dict):
        yield from _dict_parts(value, cancelled, sort_keys=sort_keys)
    else:
        yield json.dumps(value)


def _dict_parts(
    value: JSONDict, cancelled: Event | None, *, sort_keys: bool
) -> Iterator[str]:
    yield "{"
    keys = sorted(value) if sort_keys else value
    for index, key in enumerate(keys):
        if index:
            yield ", "
        yield from _string_parts(key, cancelled)
        yield ": "
        yield from _json_parts(value[key], cancelled, sort_keys=sort_keys)
    yield "}"


def replay_page_digest(messages: JSONList, cancelled: Event | None) -> bytes | None:
    """Hash canonical JSON incrementally without a whole-page serialization."""
    digest = hashlib.sha256()
    for chunk in _json_parts(messages, cancelled, sort_keys=True):
        if cancelled is not None and cancelled.is_set():
            return None
        digest.update(chunk.encode("ascii"))
    if cancelled is not None and cancelled.is_set():
        return None
    return digest.digest()


def read_replay_record(spool: IO[bytes]) -> JSONDict:
    """Read one size-bounded spool record, not an entire page."""
    line = spool.readline(MAX_REPLAY_RECORD_BYTES + 1)
    if len(line) > MAX_REPLAY_RECORD_BYTES:
        msg = "Kick replay record byte resource limit exceeded; capture is incomplete."
        raise KickResourceLimit(msg)
    return cast("JSONDict", json.loads(line))


class ReplayBudget:
    """Account for replay collection before metadata growth or spool writes."""

    def __init__(self) -> None:
        """Initialize counters for one reverse-history collection."""
        self.pages = 0
        self.records = 0
        self.cursor_bytes = 0
        self.spool_bytes = 0

    def reserve_page(self, cursor: str | None) -> None:
        """Reserve a request and its retained cursor before fetching a page."""
        check_replay_cursor(cursor)
        cursor_bytes = len(cursor.encode("utf-8")) if cursor is not None else 0
        if (
            self.pages >= MAX_REPLAY_PAGES
            or self.cursor_bytes + cursor_bytes > MAX_REPLAY_CURSOR_BYTES
        ):
            msg = (
                "Kick replay page/metadata resource limit exceeded; "
                "capture is incomplete."
            )
            raise KickResourceLimit(msg)
        self.pages += 1
        self.cursor_bytes += cursor_bytes

    def reserve_records(self, count: int) -> None:
        """Reject excess raw records before sorting or retaining page metadata."""
        if self.records + count > MAX_REPLAY_RECORDS:
            msg = "Kick replay record resource limit exceeded; capture is incomplete."
            raise KickResourceLimit(msg)
        self.records += count

    def write_page(
        self,
        spool: IO[bytes],
        messages: list[JSONDict],
        cancelled: Event | None = None,
    ) -> bool:
        """Spool oldest-first records with finite record/page expansion limits."""
        page_start = self.spool_bytes
        for message in reversed(messages):
            record_start = self.spool_bytes
            for chunk in _json_parts(message, cancelled):
                if cancelled is not None and cancelled.is_set():
                    return False
                self._write_record_chunk(spool, chunk, record_start, page_start)
            if cancelled is not None and cancelled.is_set():
                return False
            self._write_record_chunk(spool, "\n", record_start, page_start)
        return True

    def _write_record_chunk(
        self, spool: IO[bytes], chunk: str, record_start: int, page_start: int
    ) -> None:
        next_size = self.spool_bytes + len(chunk)
        if (
            next_size - record_start > MAX_REPLAY_RECORD_BYTES
            or next_size - page_start > MAX_REPLAY_PAGE_BYTES
        ):
            msg = (
                "Kick replay serialization resource limit exceeded; "
                "capture is incomplete."
            )
            raise KickResourceLimit(msg)
        self._write(spool, chunk)

    def _write(self, spool: IO[bytes], chunk: str) -> None:
        # All strings are escaped as ASCII before reaching this byte reservation.
        if self.spool_bytes + len(chunk) > MAX_REPLAY_SPOOL_BYTES:
            msg = "Kick replay spool resource limit exceeded; capture is incomplete."
            raise KickResourceLimit(msg)
        spool.write(chunk.encode("ascii"))
        self.spool_bytes += len(chunk)

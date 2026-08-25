# SPDX-License-Identifier: MIT

"""Bounded FIFO cache of recently seen message IDs for deduplication."""

from __future__ import annotations

from collections import OrderedDict

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log


def _normalize_limit(limit: int | float | str | None) -> int:
    """Return a bounded-cache limit while preserving legacy fallback rules."""
    try:
        normalized_limit = int(limit) if limit is not None else -1
    except (TypeError, ValueError):
        normalized_limit = -1

    if normalized_limit == 0:
        return DEFAULT_MAX_SEEN_MESSAGE_IDS
    if normalized_limit > 0:
        return normalized_limit

    log(
        "warning",
        f"_SeenMessageCache: ignoring invalid limit {limit!r}; "
        f"falling back to default {DEFAULT_MAX_SEEN_MESSAGE_IDS}.",
    )
    return DEFAULT_MAX_SEEN_MESSAGE_IDS


class _SeenMessageCache:
    """Track recently seen message IDs with bounded FIFO eviction."""

    def __init__(self, limit: int = DEFAULT_MAX_SEEN_MESSAGE_IDS) -> None:
        self.limit = _normalize_limit(limit)
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

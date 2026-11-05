# SPDX-License-Identifier: MIT

"""Bounded delivery identities and accounting across YouTube recovery."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, OrderedDict, deque
from typing import TYPE_CHECKING

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.utils.json_types import get_dict, get_int, get_str

if TYPE_CHECKING:
    from collections.abc import Mapping

    from chat_downloader.utils.json_types import JSONDict


def _fingerprint(value: object) -> bytes:
    """Freeze provider identity before parsing or consumer enrichment can change it."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).digest()


class ReplayPageHistory:
    """Remember exact pages under their request and advertised next tokens."""

    def __init__(self, limit: int = 128) -> None:
        """Initialize the bounded token/page history."""
        self.limit = limit
        self.pages: OrderedDict[tuple[str, bytes], None] = OrderedDict()

    def repeated(
        self, request_token: str, next_token: str | None, page: JSONDict
    ) -> bool:
        """Recognize both A→A stalls and A→B followed by the same B→B page."""
        fingerprint = _fingerprint(page)
        key = (request_token, fingerprint)
        repeated = key in self.pages
        for token in (request_token, next_token):
            if token is not None:
                identity = (token, fingerprint)
                self.pages[identity] = None
                self.pages.move_to_end(identity)
        while len(self.pages) > self.limit:
            self.pages.popitem(last=False)
        return repeated


_LIVE_PRESENTATION_FIELDS = frozenset(
    {"received_timestamp", "time_in_seconds", "time_text", "timing"}
)


class _VideoDeliveryHistory:
    """Retain occurrence counts for a bounded accepted suffix of one video."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.events: deque[bytes] = deque()
        self.counts: Counter[bytes] = Counter()

    def record(self, identity: bytes) -> None:
        self.events.append(identity)
        self.counts[identity] += 1
        if len(self.events) > self.limit:
            oldest = self.events.popleft()
            self.counts[oldest] -= 1
            if not self.counts[oldest]:
                del self.counts[oldest]


class BootstrapOverlap:
    """Consume prior occurrences only in a reacquired child's bootstrap prefix."""

    def __init__(self, history: _VideoDeliveryHistory) -> None:
        """Snapshot prior occurrences for one reacquired child."""
        self.history = history
        self.remaining = history.counts.copy()

    def accept(self, item: JSONDict) -> bool:
        """Keep new payloads, event kinds, and ordinary within-child repetitions."""
        if not (get_str(item, "message_id") or get_str(item, "target_message_id")):
            return True
        identity = _fingerprint(
            {
                key: value
                for key, value in item.items()
                if key not in _LIVE_PRESENTATION_FIELDS
            }
        )
        if self.remaining:
            if self.remaining[identity]:
                self.remaining[identity] -= 1
                if not self.remaining[identity]:
                    del self.remaining[identity]
                return False
            # The first new identifiable event ends bootstrap overlap. Later
            # identical updates are real occurrences, not a stream-wide dedup.
            self.remaining.clear()
        self.history.record(identity)
        return True


class ChannelDeliveryHistory:
    """Keep at most five videos, each with 10,000 accepted event occurrences."""

    def __init__(
        self, *, max_videos: int = 5, max_events: int = DEFAULT_MAX_SEEN_MESSAGE_IDS
    ) -> None:
        """Initialize bounded histories scoped by video identity."""
        self.max_videos = max_videos
        self.max_events = max_events
        self.videos: OrderedDict[str, _VideoDeliveryHistory] = OrderedDict()

    def begin(self, video_id: str) -> BootstrapOverlap:
        """Snapshot this video's overlap without blacklisting a failed child."""
        history = self.videos.pop(video_id, None)
        if history is None:
            history = _VideoDeliveryHistory(self.max_events)
        self.videos[video_id] = history
        if len(self.videos) > self.max_videos:
            self.videos.popitem(last=False)
        return BootstrapOverlap(history)


class ChildDiagnostics:
    """Project a child's running totals onto finalized prior-child totals."""

    def __init__(self, target: dict[str, object]) -> None:
        """Snapshot accumulated totals before observing a new child."""
        self.target = target
        self.baseline = {
            key: value.copy() if isinstance(value, dict) else value
            for key, value in target.items()
        }

    def update(self, source: Mapping[str, object]) -> None:
        """Refresh without overwriting prior children or double-counting updates."""
        for key, value in source.items():
            if type(value) is int and (key.endswith("_count") or key == "parse_error"):
                self.target[key] = get_int(self.baseline, key) + value
            elif key == "non_emission_counts":
                counts = dict(get_dict(self.baseline, key))
                for reason, count in get_dict(source, key).items():
                    if type(count) is int:
                        counts[reason] = get_int(counts, reason) + count
                self.target[key] = counts
            elif key == "initial_request_profile":
                self.target.setdefault(key, value)
            else:
                self.target[key] = value

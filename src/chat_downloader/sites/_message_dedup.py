# SPDX-License-Identifier: MIT

"""Formatted-message deduplication and bounded optional console state policy."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import log

from ._seen_cache import _SeenMessageCache

# Paid events can appear once in the chat stream and again in the ticker. Raw
# outputs retain both provider events; formatted outputs present one semantic
# message to the user.
SUPERCHAT_DEDUP_TYPES = frozenset(
    {
        "paid_message",
        "ticker_paid_message_item",
        "paid_sticker",
        "ticker_paid_sticker_item",
        "membership_item",
        "ticker_sponsor_item",
    },
)


class _FormattedMessageDeduplicator:
    """Decide whether a chat item belongs in a formatted output stream."""

    def __init__(
        self,
        max_seen_message_ids: int | None = DEFAULT_MAX_SEEN_MESSAGE_IDS,
    ) -> None:
        limit = 0 if max_seen_message_ids is None else max_seen_message_ids
        self._seen_message_cache = _SeenMessageCache(limit)

    def should_emit(self, item: dict[str, Any]) -> bool:
        """Return whether *item* should be emitted to formatted sinks."""
        message_type = item.get("message_type")
        message_id = item.get("message_id")
        if (
            message_type not in SUPERCHAT_DEDUP_TYPES
            or not isinstance(message_id, str)
            or not message_id
        ):
            return True

        is_new, evicted_message_id = self._seen_message_cache.register(message_id)
        if evicted_message_id is not None:
            log(
                "debug",
                "Dedup cache limit reached "
                f"({self._seen_message_cache.limit}); "
                f"evicting message_id={evicted_message_id!r}.",
            )
        return is_new


@dataclass(frozen=True, slots=True)
class _ConsoleState:
    """Provider-selected snapshot and optional lifecycle display requirement."""

    key: str
    payload: object
    always_display: bool = False


class _ConsoleStateDeduplicator:
    """Remember only the latest payload for up to 128 provider-selected states."""

    def __init__(self, max_states: int = 128) -> None:
        self._max_states = max_states
        self._states: OrderedDict[bytes, bytes] = OrderedDict()

    def should_emit(self, state: _ConsoleState | None) -> bool:
        """Suppress exact unchanged state; malformed data always remains visible."""
        if state is None or self._max_states <= 0:
            return True
        try:
            identity = hashlib.sha256(json.dumps(state.key).encode("utf-8")).digest()
            fingerprint = hashlib.sha256(
                json.dumps(state.payload, sort_keys=True, allow_nan=False).encode(
                    "utf-8"
                )
            ).digest()
        except (TypeError, ValueError, RecursionError):
            self._states.clear()
            return True
        previous = self._states.pop(identity, None)
        self._states[identity] = fingerprint
        if len(self._states) > self._max_states:
            self._states.popitem(last=False)
        return state.always_display or previous != fingerprint

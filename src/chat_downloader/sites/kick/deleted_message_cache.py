# SPDX-License-Identifier: MIT

"""Bounded original-message context for Kick live deletion notices."""

from __future__ import annotations

from collections import OrderedDict
from sys import getsizeof
from typing import TYPE_CHECKING

from chat_downloader.utils.json_types import get_dict, get_str

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

_DELETED_MESSAGE_CACHE_LIMIT = 10_000
_DELETED_MESSAGE_CACHE_BYTE_LIMIT = 16 * 1024 * 1024


class _DeletedMessageCache:
    """Retain only IDs, text, and author names with FIFO eviction.

    The byte budget covers retained strings, including their object overhead.
    Entry/container overhead is bounded separately by the entry limit.
    """

    def __init__(
        self,
        *,
        limit: int = _DELETED_MESSAGE_CACHE_LIMIT,
        max_bytes: int = _DELETED_MESSAGE_CACHE_BYTE_LIMIT,
    ) -> None:
        self._limit = limit
        self._max_bytes = max_bytes
        self._bytes = 0
        self._messages: OrderedDict[str, tuple[str, str, int]] = OrderedDict()

    def observe(self, message: JSONDict) -> None:
        """Remember chat text or attach cached context to a deletion event."""
        if message.get("message_type") == "text_message":
            self._remember(message)
        elif message.get("message_type") == "message_deleted":
            metadata = get_dict(message, "metadata")
            original = self._messages.get(get_str(metadata, "deleted_message_id"))
            if original is not None:
                text, author, _size = original
                metadata["deleted_message_text"] = text
                if author:
                    metadata["deleted_message_author"] = author

    def _remember(self, message: JSONDict) -> None:
        message_id = get_str(message, "message_id")
        text = get_str(message, "message")
        if not message_id or not text or message_id in self._messages:
            return
        author_info = get_dict(message, "author")
        author = get_str(author_info, "display_name") or get_str(author_info, "name")
        size = sum(getsizeof(value) for value in (message_id, text, author))
        if size > self._max_bytes or self._limit <= 0:
            return
        while self._messages and (
            len(self._messages) >= self._limit or self._bytes + size > self._max_bytes
        ):
            _message_id, (_text, _author, evicted_size) = self._messages.popitem(
                last=False
            )
            self._bytes -= evicted_size
        self._messages[message_id] = (text, author, size)
        self._bytes += size

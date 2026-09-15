# SPDX-License-Identifier: MIT

"""Bounded enrichment of sparse YouTube tickers from their paid chat events."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy

from chat_downloader.utils.json_types import JSONDict, get_dict

_TICKER_TYPES = {
    "ticker_paid_message_item": "paid_message",
    "ticker_paid_sticker_item": "paid_sticker",
}
_DETAIL_FIELDS = ("message", "money", "author", "emotes", "sticker_images", "timestamp")


class PaidEventCache:
    """Remember up to 10,000 paid events within one retrieval, before filtering.

    Only absent ticker details are filled, for a matching ID and paid type.
    Timing and action identity stay with the ticker; no match means no guess.
    Copies isolate the cache from callers mutating yielded messages.
    """

    def __init__(self, limit: int = 10_000) -> None:
        """Set the maximum number of remembered paid event IDs."""
        self._limit = max(0, limit)
        self._events: OrderedDict[str, tuple[str, JSONDict]] = OrderedDict()

    def enrich(self, message: JSONDict) -> None:
        """Remember a paid item or fill a matching sparse ticker in place."""
        message_id = message.get("message_id")
        kind = message.get("message_type")
        if (
            not isinstance(message_id, str)
            or not message_id
            or not isinstance(kind, str)
        ):
            return
        if kind in _TICKER_TYPES.values():
            details = {key: message[key] for key in _DETAIL_FIELDS if key in message}
            self._events[message_id] = (kind, deepcopy(details))
            self._events.move_to_end(message_id)
            if len(self._events) > self._limit:
                self._events.popitem(last=False)
            return
        prior = self._events.get(message_id)
        if prior is None or _TICKER_TYPES.get(kind) != prior[0]:
            return
        cached_author = get_dict(prior[1], "author")
        ticker_author = get_dict(message, "author")
        if (
            cached_author.get("id")
            and ticker_author.get("id")
            and cached_author["id"] != ticker_author["id"]
        ):
            return
        _fill_missing_details(message, prior[1])


def _fill_missing_details(message: JSONDict, details: JSONDict) -> None:
    """Copy missing fields without aliasing cached data or replacing values."""
    for key, value in details.items():
        if key == "author":
            author = get_dict(message, "author")
            message["author"] = author
            for field, detail in get_dict(details, "author").items():
                if author.get(field) is None or author[field] == "":
                    author[field] = deepcopy(detail)
        elif message.get(key) is None or message[key] == "":
            message[key] = deepcopy(value)

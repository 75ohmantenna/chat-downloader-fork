# SPDX-License-Identifier: MIT

"""Twitch type definitions for badge caching.

These types provide explicit, instance-owned badge caches that are passed
through parsing functions without relying on module-level badge state.

Usage
-----
The extractor owns a ``BadgeCache`` and populates it after fetching badge
data from the Twitch GraphQL API::

    self.badge_cache = BadgeCache()
    update_badge_info(
        ..., self.badge_cache.global_badges, self.badge_cache.channel_badges
    )

Before parsing a batch of messages, take a snapshot once and pass it through::

    badge_set = self.badge_cache.snapshot()
    _parse_item(node, offset, channel_id, badge_set=badge_set)

Parsing functions must use the injected ``badge_set`` as the primary source
of fetched badge metadata. Callers that omit it still receive normalized badge
names and versions, but not cached icon metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BadgeSet:
    """Badge-data snapshot treated as read-only by parsing code.

    The dataclass and its dictionaries are mutable; BadgeCache.snapshot() copies
    lookup dictionaries while sharing individual badge metadata dictionaries.

    Attributes:
        global_badges: Global badges keyed by ``(setID, version)`` tuples.
        channel_badges: Channel-specific badges keyed by *channelID*, then
            ``(setID, version)`` tuples.
    """

    global_badges: dict[tuple[str, str], dict[str, Any]]
    channel_badges: dict[str, dict[tuple[str, str], dict[str, Any]]]


@dataclass
class BadgeCache:
    """Mutable badge cache owned by a ``TwitchChatDownloader`` instance.

    Holds badge data fetched from Twitch's GraphQL API.  Use
    :meth:`snapshot` to obtain a :class:`BadgeSet` for passing into parsing
    functions.

    Attributes:
        global_badges: Global badges keyed by ``(setID, version)`` tuples.
            Mutated in-place by :func:`~.badge_client.update_badge_info`.
        channel_badges: Channel-specific subscriber badges keyed by
            *channelID*, then ``(setID, version)`` tuples.
            Mutated in-place by :func:`~.badge_client.update_badge_info`.
    """

    global_badges: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    channel_badges: dict[str, dict[tuple[str, str], dict[str, Any]]] = field(
        default_factory=dict,
    )

    def snapshot(self) -> BadgeSet:
        """Return a :class:`BadgeSet` snapshot suitable for parsing.

        Copies global and per-channel lookup dictionaries, isolating additions
        and replacements from an in-progress parse. Individual badge metadata
        dictionaries remain shared and are treated as read-only by parsing code.

        Returns:
            :class:`BadgeSet` with shallow copies of the current badge data.
        """
        return BadgeSet(
            global_badges=dict(self.global_badges),
            channel_badges={k: dict(v) for k, v in self.channel_badges.items()},
        )

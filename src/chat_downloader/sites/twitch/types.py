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

# Bound provider reuse and the work needed to copy a capture's badge snapshot.
_BADGE_CHANNEL_LIMIT = 128


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

    def refreshed(self, cache: BadgeCache, channel_id: str | None) -> BadgeSet:
        """Refresh receiving/global metadata without growing capture source keys."""
        channels = {key: dict(value) for key, value in self.channel_badges.items()}
        if channel_id and channel_id in channels and channel_id in cache.channel_badges:
            channels[channel_id] = dict(cache.channel_badges[channel_id])
        return BadgeSet(dict(cache.global_badges), channels)


@dataclass
class BadgeCache:
    """Mutable badge cache owned by a ``TwitchChatDownloader`` instance.

    Holds badge data fetched from Twitch's GraphQL API for at most
    ``_BADGE_CHANNEL_LIMIT`` recently refreshed channels. Use :meth:`snapshot`
    to obtain a :class:`BadgeSet` for passing into parsing functions.

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

    def snapshot(self, channel_id: str | None = None) -> BadgeSet:
        """Return a :class:`BadgeSet` snapshot suitable for parsing.

        Copies global and per-channel lookup dictionaries, isolating refreshes
        and LRU eviction from an in-progress parse. Individual badge metadata
        dictionaries remain shared; refreshes replace rather than mutate them.
        A receiving channel reserves one of the bounded slots even when its
        initial refresh failed, so later reconnects never enlarge the key set.

        Returns:
            :class:`BadgeSet` with shallow copies of the current badge data.
        """
        channels = {key: dict(value) for key, value in self.channel_badges.items()}
        if channel_id and channel_id not in channels:
            if len(channels) >= _BADGE_CHANNEL_LIMIT:
                channels.pop(next(iter(channels)))
            channels[channel_id] = {}
        return BadgeSet(
            global_badges=dict(self.global_badges),
            channel_badges=channels,
        )

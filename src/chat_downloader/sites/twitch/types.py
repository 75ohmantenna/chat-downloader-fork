# SPDX-License-Identifier: MIT

"""Twitch type definitions for badge caching.

These types provide explicit, instance-owned badge caches that are passed
through parsing functions for deterministic, pure parsing without relying on
module-level global state.

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
of badge data.  Module-level globals in ``parsing.messages`` remain only as
a compatibility fallback for callers that invoke parsing functions directly
without providing a badge_set (e.g. third-party integrations).  They will be
removed in a future version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BadgeSet:
    """Immutable snapshot of badge data for use during parsing.

    ``badge_set`` is required for deterministic parsing; module globals in
    ``parsing.messages`` remain only as a compatibility fallback (to be
    removed in a future version).

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
            Mutated in-place by :func:`~.client.update_badge_info`.
        channel_badges: Channel-specific subscriber badges keyed by
            *channelID*, then ``(setID, version)`` tuples.
            Mutated in-place by :func:`~.client.update_badge_info`.
    """

    global_badges: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    channel_badges: dict[str, dict[tuple[str, str], dict[str, Any]]] = field(
        default_factory=dict,
    )

    def snapshot(self) -> BadgeSet:
        """Return a :class:`BadgeSet` snapshot suitable for parsing.

        Copies the top-level dicts so that any subsequent update to the cache
        does not affect an in-progress parse.  Inner badge dicts are *not*
        deep-copied; they are treated as read-only by parsing code.

        Returns:
            :class:`BadgeSet` with shallow copies of the current badge data.
        """
        return BadgeSet(
            global_badges=dict(self.global_badges),
            channel_badges={k: dict(v) for k, v in self.channel_badges.items()},
        )

# SPDX-License-Identifier: MIT

r"""Drift regression harness for Twitch IRC and GraphQL chat parsing.

Each fixture in ``tests/fixtures/twitch/live_events/`` supplies a raw IRC
message. Parsing must produce the expected message type without logging drift.

Each fixture in ``tests/fixtures/twitch/graphql/`` is likewise replayed through
the VOD edge processor and must yield its message ID without logging drift.

Workflow for new drift
----------------------
1. Capture a raw IRC message or GraphQL edge that reports drift at runtime.
2. Identify the cause (unknown action type, unknown message type, etc.).
3. Fix the parsing code.
4. Add the reviewed fixture to the matching ``live_events/`` or ``graphql/``
   directory and name it descriptively.
5. Record the expected IRC message type or replay message ID and run this harness.

GraphQL hash-rotation guard
---------------------------
A separate test asserts that every ``operationName`` used anywhere in the
Twitch client code has a corresponding entry in ``OPERATION_HASHES``.  When
Twitch rotates hashes, updating ``OPERATION_HASHES`` in
``src/chat_downloader/sites/twitch/constants.py`` is the only change needed;
the guard flags immediately if a caller references an unlisted operation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

import chat_downloader.sites.twitch.parsing.message_emotes as _me
import chat_downloader.sites.twitch.parsing.message_irc_resolve as _mir
import chat_downloader.sites.twitch.parsing.messages as _messages
import chat_downloader.sites.twitch.replay_service as _replay
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.twitch.constants import (
    MESSAGE_GROUPS,
    MESSAGE_REGEX,
    OPERATION_HASHES,
    build_known_irc_keys,
)
from chat_downloader.sites.twitch.types import BadgeSet
from chat_downloader.utils.json_types import dig

_FX_DIR = Path(__file__).resolve().parent / "fixtures" / "twitch" / "live_events"
_GQL_FX_DIR = Path(__file__).resolve().parent / "fixtures" / "twitch" / "graphql"


_EXPECTED_IRC_TYPES = {
    "irc-clearchat-timeout.json": "ban_user",
    "irc-clearmsg-deleted-message.json": "delete_message",
    "irc-privmsg-action.json": "text_message",
    "irc-privmsg-animated-message.json": "animated-message",
    "irc-privmsg-crowd-chant.json": "text_message",
    "irc-privmsg-gif.json": "text_message",
    "irc-privmsg-japanese-badge.json": "text_message",
    "irc-privmsg-paid-pinned-chat.json": "text_message",
    "irc-privmsg-shared-chat.json": "text_message",
    "irc-privmsg-with-emotes.json": "text_message",
    "irc-roomstate-follower-slow.json": "room_state",
    "irc-usernotice-announcement.json": "announcement",
    "irc-usernotice-charity-donation.json": "charity_donation",
    "irc-usernotice-gift-sub-base-match.json": "gift_subscription_match",
    "irc-usernotice-moderator-anniversary.json": "moderator_anniversary",
    "irc-usernotice-modiversary.json": "moderator_anniversary",
    "irc-usernotice-one-tap-breakpoint.json": "one_tap_breakpoint_achieved",
    "irc-usernotice-one-tap-gift-redeemed.json": "one_tap_gift_redeemed",
    "irc-usernotice-one-tap-streak-expired.json": "one_tap_streak_expired",
    "irc-usernotice-one-tap-streak-started.json": "one_tap_streak_started",
    "irc-usernotice-social-sharing-badge.json": "social_sharing_badge",
    "irc-usernotice-subscription-goal-multimonth.json": "subscription",
}

_EXPECTED_REPLAY_IDS = {
    "replay-comment-badge-id.json": "badge-id-message",
    "replay-comment-basic.json": "message-1",
    "replay-comment-mobile.json": "mobile-message-1",
}


@pytest.fixture
def drift_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any unexpected-data debug call in Twitch parsing composition."""
    seen: list[str] = []

    def _spy(*args: Any, **_kwargs: Any) -> None:
        seen.append(" ".join(str(a) for a in args))

    monkeypatch.setattr(_mir, "debug_log", _spy)
    monkeypatch.setattr(_me, "debug_log", _spy)
    monkeypatch.setattr(_messages, "debug_log", _spy)
    monkeypatch.setattr(_replay, "debug_log", _spy)
    return seen


@pytest.mark.parametrize(
    "name",
    sorted({path.name for path in _FX_DIR.glob("*.json")} | _EXPECTED_IRC_TYPES.keys()),
)
def test_irc_fixture_parses_without_drift(name: str, drift_recorder: list[str]) -> None:
    """All curated IRC fixtures must parse without triggering drift logs."""
    payload = json.loads((_FX_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{name} must contain a fixture object"
    raw = dig(payload, "raw")
    assert isinstance(raw, str), f"{name} must contain a raw IRC string"
    assert name in _EXPECTED_IRC_TYPES, f"{name} needs an expected message type"
    match = MESSAGE_REGEX.search(raw)
    if match is None:
        pytest.fail(f"{name}: MESSAGE_REGEX did not match the raw string")
    badge_set = BadgeSet(global_badges={}, channel_badges={})
    parsed = _messages._parse_irc_item(match, badge_set=badge_set)
    unexpected_keys = parsed.keys() - build_known_irc_keys()
    assert not drift_recorder, f"{name} triggered drift log(s):\n" + "\n".join(
        f"  {line}" for line in drift_recorder
    )
    assert not unexpected_keys, (
        f"{name} produced unexpected parsed keys: {sorted(unexpected_keys)}"
    )
    assert parsed.get("message_type") == _EXPECTED_IRC_TYPES[name]


@pytest.mark.parametrize(
    "name",
    sorted(
        {path.name for path in _GQL_FX_DIR.glob("*.json")} | _EXPECTED_REPLAY_IDS.keys()
    ),
)
def test_graphql_fixture_parses_without_drift(
    name: str,
    drift_recorder: list[str],
) -> None:
    payload = json.loads((_GQL_FX_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{name} must contain a fixture object"
    edge = payload.get("raw")
    assert isinstance(edge, dict), f"{name} must contain a raw GraphQL edge"
    assert name in _EXPECTED_REPLAY_IDS, f"{name} needs an expected replay message ID"

    drift_logger = logging.getLogger("twitch-drift-harness")
    drift_logger.setLevel(logging.DEBUG)
    data, disposition = _replay._process_vod_edge(
        edge,
        offset=0.0,
        creator_channel_id="123",
        badge_set=BadgeSet(global_badges={}, channel_badges={}),
        time_filter=TimeRangeFilter(),
        msg_filter=MessageFilter(MESSAGE_GROUPS, groups_to_add=["all"]),
        logger_obj=drift_logger,
    )

    assert disposition == "yield"
    assert data is not None
    assert data.get("message_id") == _EXPECTED_REPLAY_IDS[name]
    assert not drift_recorder, f"{name} triggered drift log(s): {drift_recorder}"
    expected_badges = payload.get("expected_author_badges")
    if expected_badges is not None:
        author = data.get("author")
        assert isinstance(author, dict)
        assert author.get("badges") == expected_badges


# ---------------------------------------------------------------------------
# GraphQL hash-rotation guard
# ---------------------------------------------------------------------------

# Every operationName referenced in Twitch client code must have a hash entry.
# When Twitch rotates hashes, update OPERATION_HASHES in constants.py.
_EXPECTED_OPERATIONS: frozenset[str] = frozenset(
    {
        "BrowsePage_Popular",
        "BroadcastBadges",
        "ChatList_Badges",
        "ClipsCards__User",
        "FilterableVideoTower_Videos",
        "GlobalBadges",
        "GlobalBadgesMobile",
        "StreamMetadata",
        "VideoCommentsByOffsetOrCursor",
        "VideoCommentsQuery",
        "VideoMetadata",
    }
)


def test_operation_hashes_covers_all_used_operations() -> None:
    """OPERATION_HASHES must contain every operationName used in client code."""
    missing = _EXPECTED_OPERATIONS - OPERATION_HASHES.keys()
    assert not missing, (
        "OPERATION_HASHES is missing entries for: "
        + ", ".join(sorted(missing))
        + ".  Update constants.py when Twitch rotates these hashes."
    )


def test_operation_hashes_has_no_orphaned_entries() -> None:
    """OPERATION_HASHES must not contain entries unused by client code."""
    orphaned = OPERATION_HASHES.keys() - _EXPECTED_OPERATIONS
    assert not orphaned, (
        "OPERATION_HASHES has orphaned (unreferenced) entries: "
        + ", ".join(sorted(orphaned))
        + ".  Remove them or add the missing caller."
    )

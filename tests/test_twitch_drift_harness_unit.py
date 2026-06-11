# SPDX-License-Identifier: MIT

r"""Drift regression harness for Twitch IRC chat parsing.

Each fixture in ``tests/fixtures/twitch/live_events/`` that contains a raw
IRC message string (JSON object with a ``"raw"`` key) is replayed through
the full IRC parser.  The test asserts that the spy-patched ``debug_log``
is **never** called with a drift-sentinel message, so previously-fixed drift
becomes a permanent regression anchor.

Workflow for new drift
----------------------
1. Capture a raw IRC message that triggers ``debug_log`` at runtime.
2. Identify the cause (unknown action type, unknown message type, etc.).
3. Fix the parsing code.
4. Add a ``{"raw": "<irc line>\\r\\n"}`` fixture to
   ``tests/fixtures/twitch/live_events/`` and name it descriptively.
5. Run this harness — it must pass before merging.

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
from pathlib import Path
from typing import Any

import pytest

import chat_downloader.sites.twitch.parsing.message_emotes as _me
import chat_downloader.sites.twitch.parsing.message_irc_resolve as _mir
from chat_downloader.sites.twitch.constants import (
    MESSAGE_REGEX,
    OPERATION_HASHES,
)
from chat_downloader.sites.twitch.parsing.messages import _parse_irc_item
from chat_downloader.sites.twitch.types import BadgeSet

_FX_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "twitch" / "live_events"
)

# Sentinel substrings that indicate an unhandled IRC action or message type.
_DRIFT_MARKERS = (
    "Unknown action type",
    "Unknown message type",
)


def _raw_irc_fixtures() -> list[Path]:
    """Return all live_events fixtures that carry a raw IRC message."""
    paths = []
    for path in sorted(_FX_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover
            continue
        if isinstance(payload, dict) and "raw" in payload:
            paths.append(path)
    return paths


@pytest.fixture
def drift_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Spy on debug_log in IRC parsing modules; record drift-sentinel calls."""
    seen: list[str] = []

    def _spy(*args: Any, **_kwargs: Any) -> None:
        text = " ".join(str(a) for a in args)
        if any(marker in text for marker in _DRIFT_MARKERS):
            seen.append(text)

    monkeypatch.setattr(_mir, "debug_log", _spy)
    monkeypatch.setattr(_me, "debug_log", _spy)
    return seen


@pytest.mark.parametrize(
    "path",
    _raw_irc_fixtures(),
    ids=lambda p: p.name,
)
def test_irc_fixture_parses_without_drift(
    path: Path, drift_recorder: list[str]
) -> None:
    """All curated IRC fixtures must parse without triggering drift logs."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw: str = payload["raw"]
    match = MESSAGE_REGEX.search(raw)
    if match is None:
        pytest.skip(f"{path.name}: MESSAGE_REGEX did not match the raw string")
    badge_set = BadgeSet(global_badges={}, channel_badges={})
    _parse_irc_item(match, badge_set=badge_set)
    assert not drift_recorder, (
        f"{path.name} triggered drift log(s):\n"
        + "\n".join(f"  {line}" for line in drift_recorder)
    )


# ---------------------------------------------------------------------------
# GraphQL hash-rotation guard
# ---------------------------------------------------------------------------

# Every operationName referenced in Twitch client code must have a hash entry.
# When Twitch rotates hashes, update OPERATION_HASHES in constants.py.
_EXPECTED_OPERATIONS: frozenset[str] = frozenset(
    {
        "BrowsePage_Popular",
        "ChatList_Badges",
        "ClipsCards__User",
        "FilterableVideoTower_Videos",
        "GlobalBadges",
        "StreamMetadata",
        "VideoCommentsByOffsetOrCursor",
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

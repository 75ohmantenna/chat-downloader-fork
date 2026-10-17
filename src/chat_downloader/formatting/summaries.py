# SPDX-License-Identifier: MIT

"""Pure text summaries for counts, rankings, progress, and prediction state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat_downloader.utils.json_types import get_bool, get_int, get_list, get_str

if TYPE_CHECKING:
    from collections.abc import Mapping


def format_nonnegative_integer(value: object) -> str | None:
    """Render integer counts, retaining zero and rejecting malformed values."""
    return str(value) if type(value) is int and value >= 0 else None


def format_leaderboard(value: object, unit: str) -> str:
    """Show the first three supplied entries of each explicitly enabled period."""
    if not isinstance(value, dict):
        return ""
    periods = (("week", "Weekly"), ("month", "Monthly"), ("lifetime", "Lifetime"))
    fragments = []
    for period, label in periods:
        key = f"gifts_{period}"
        entries = value.get(key)
        if not get_bool(value, f"{key}_enabled") or not isinstance(entries, list):
            continue
        leaders = []
        for entry in entries[:3]:
            if not isinstance(entry, dict):
                continue
            name = get_str(entry, "username").strip()
            quantity = get_int(entry, "quantity", -1)
            if name and quantity >= 0:
                leaders.append(f"{name} ({quantity:,} {unit})")
        if leaders or not entries:
            fragments.append(f"{label}: {', '.join(leaders) if leaders else 'empty'}")
    return "".join(f" | {fragment}" for fragment in fragments)


def format_goal(value: object) -> str:
    """Show available nonnegative integer progress and status without guessing."""
    if not isinstance(value, dict):
        return ""
    kind = get_str(value, "type").strip().replace("_", " ").capitalize() or "Progress"
    current = get_int(value, "current_value", -1)
    target = get_int(value, "target_value", -1)
    fragments = []
    if current >= 0:
        progress = f"{current:,} / {target:,}" if target >= 0 else f"{current:,}"
        fragments.append(f"{kind}: {progress}")
    elif target >= 0:
        fragments.append(f"{kind}: target {target:,}")
    status = get_str(value, "status").strip()
    if status:
        fragments.append(f"Status: {status}")
    return "".join(f" | {fragment}" for fragment in fragments)


def _prediction_outcome_label(outcome: Mapping[str, object], position: int) -> str:
    """Use a supplied title or an explicit positional label."""
    return get_str(outcome, "title").strip() or f"Outcome {position + 1}"


def _prediction_votes(count: int) -> str:
    """Keep unavailable counts distinct from a valid zero."""
    if count < 0:
        return "votes unknown"
    return f"{count:,} {'vote' if count == 1 else 'votes'}"


def format_prediction(value: object) -> str:
    """Summarize outcomes without inferring missing counts or a winning result."""
    if not isinstance(value, dict):
        return ""
    fragments = []
    for field, label in (("title", "Question"), ("state", "State")):
        text = get_str(value, field).strip()
        if text:
            fragments.append(f"{label}: {text}")
    outcomes = get_list(value, "outcomes")
    counts = [
        get_int(outcome, "vote_count", -1) if isinstance(outcome, dict) else -1
        for outcome in outcomes
    ]
    for position, outcome in enumerate(outcomes):
        if not isinstance(outcome, dict):
            continue
        label = _prediction_outcome_label(outcome, position)
        fragments.append(f"{label}: {_prediction_votes(counts[position])}")
    if counts and min(counts) >= 0:
        fragments.append(f"Total: {_prediction_votes(sum(counts))}")
    winner = get_str(value, "winning_outcome_id")
    matches = [
        (position, outcome)
        for position, outcome in enumerate(outcomes)
        if isinstance(outcome, dict) and get_str(outcome, "id") == winner
    ]
    if (
        get_str(value, "state").strip() == "RESOLVED"
        and winner.strip()
        and len(matches) == 1
    ):
        position, outcome = matches[0]
        fragments.append(f"Winner: {_prediction_outcome_label(outcome, position)}")
    return "".join(f" | {fragment}" for fragment in fragments)

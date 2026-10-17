# SPDX-License-Identifier: MIT

"""Pure, bounded text summaries for structured ranking and progress fields."""

from __future__ import annotations

from chat_downloader.utils.json_types import get_bool, get_int, get_str


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

# SPDX-License-Identifier: MIT

"""Pure, bounded text summaries for structured ranking and progress fields."""

from __future__ import annotations

from chat_downloader.utils.json_types import get_bool, get_int, get_str


def format_count(value: object) -> str:
    """Render nonnegative integer counts, including decimal strings and zero."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return value
    return ""


def format_viewer_count(value: object) -> str:
    """Render a viewer snapshot while distinguishing offline and unknown counts."""
    if not isinstance(value, dict):
        return "Viewer count"
    payload = value.get("data")
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        count = get_int(payload[0], "viewers", -1)
        if count >= 0:
            return f"Viewer count: {count}"
    if (
        payload == []
        and value.get("source") == "public_rest"
        and value.get("offline") is True
    ):
        return "Viewer count: offline"
    return "Viewer count"


def _leaderboard_entries(entries: list[object], unit: str) -> str:
    """Render valid leaders within the first three supplied ranking slots."""
    leaders = []
    for entry in entries[:3]:
        if not isinstance(entry, dict):
            continue
        name = get_str(entry, "username").strip()
        quantity = get_int(entry, "quantity", -1)
        if name and quantity >= 0:
            leaders.append(f"{name} ({quantity:,} {unit})")
    return ", ".join(leaders)


def format_leaderboard(
    value: object, unit: str, *, subscription_gifts: bool = False
) -> str:
    """Show supplied subscription rankings or explicitly enabled gift periods."""
    if not isinstance(value, dict):
        return ""
    periods = (
        ("week", "Weekly", "weekly_leaderboard"),
        ("month", "Monthly", "monthly_leaderboard"),
        ("lifetime", "Lifetime", "leaderboard"),
    )
    fragments = []
    for period, period_label, subscription_key in periods:
        label = period_label
        key = f"gifts_{period}"
        if subscription_gifts and subscription_key in value:
            key = subscription_key
            if period == "lifetime":
                label = "Overall"
        elif not get_bool(value, f"{key}_enabled"):
            continue
        entries = value.get(key)
        if not isinstance(entries, list):
            continue
        leaders = _leaderboard_entries(entries, unit)
        if leaders or not entries:
            fragments.append(f"{label}: {leaders or 'empty'}")
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

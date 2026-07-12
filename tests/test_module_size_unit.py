# SPDX-License-Identifier: MIT

"""Guard against module line count regressions post-split."""

from __future__ import annotations

from pathlib import Path

MAX_LINES = 400
# Intentionally large data tables and cohesive single-purpose modules;
# see docs/maintenance-notes.md for rationale.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # Data tables — line counts are dominated by literal mappings.
        "sites/youtube/constants_message.py",
        "sites/twitch/remappings.py",
        "sites/twitch/constants.py",
    }
)
SRC = Path(__file__).resolve().parents[1] / "src" / "chat_downloader"


def test_no_module_exceeds_line_budget() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWLIST:
            continue
        n = sum(1 for _ in path.open(encoding="utf-8"))
        if n > MAX_LINES:
            offenders.append(f"{rel}: {n}")
    assert not offenders, "modules over budget:\n" + "\n".join(offenders)

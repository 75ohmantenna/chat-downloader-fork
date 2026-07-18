# SPDX-License-Identifier: MIT

"""Guard against module line count regressions.

The line budget is a *smell signal, not a splitting trigger*: split by cohesion,
not to duck under the ceiling. Cohesive units that legitimately exceed it are
allowlisted with a rationale rather than fragmented by phase. See
docs/maintenance-decisions.md and the "Decomposition policy" in AGENTS.md.
"""

from __future__ import annotations

from pathlib import Path

MAX_LINES = 400
# Intentionally large data tables and cohesive single-purpose modules;
# See docs/maintenance-decisions.md for the governing rationale.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # Data tables — line counts are dominated by literal mappings.
        "sites/youtube/constants_message.py",
        "sites/twitch/remappings.py",
        "sites/twitch/constants.py",
        # Cohesive continuation loop: setup + request/response + iteration for
        # one YouTube chat run. Previously fragmented across six phase-modules
        # (chat_streams_{context,response,runtime_iteration}, continuation_loop
        # {,_runtime,_state}); reunified for locality. Pure helpers that don't
        # need the loop's state live in continuation_helpers.py.
        "sites/youtube/continuation.py",
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

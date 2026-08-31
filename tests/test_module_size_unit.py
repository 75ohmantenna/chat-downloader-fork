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
        # Cohesive Kick live service: metadata resolution, preloaded-history
        # assembly, and the bounded WebSocket/key-refresh loop form one stream
        # lifecycle. Splitting those phases would obscure their shared retry,
        # deduplication, and transport-close invariants.
        "sites/kick/live_service.py",
        # Cohesive Kick clip replay: web/mobile contract validation, fallback
        # evidence reconciliation, and relative-window assembly form one
        # security boundary. Splitting the lookup phases would obscure when
        # cross-endpoint identity and bounds must fail closed.
        "sites/kick/clip_service.py",
        # Cohesive Twitch replay service: metadata, bounded cursor pagination,
        # edge validation, and badge-channel identity form one replay contract.
        # Splitting request phases would obscure cursor and creator-ID state.
        "sites/twitch/replay_service.py",
        # Cohesive security boundary: structured/string redaction and secure
        # debug-sample creation share the same secret-classification rules.
        # Splitting file creation from sanitization would duplicate or weaken
        # the invariant that captured payloads are scrubbed before persistence.
        "redaction.py",
        # Cohesive offline parity state machine: input identity, physical-line
        # validity, semantic deduplication, rendering, and comparison share one
        # streaming alignment state. The privacy-safe CLI/reporting layer is
        # already separate under scripts/.
        "output/capture_parity.py",
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

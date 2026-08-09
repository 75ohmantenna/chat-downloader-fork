# SPDX-License-Identifier: MIT

"""Contract checks for scheduled stable-provider validation."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_schedules_stable_network_contracts_without_gating_pushes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "  schedule:\n" in workflow
    assert "  network-replay:\n" in workflow
    assert "if: github.event_name != 'schedule'" in workflow
    network_condition = "if: github.event_name == 'schedule'"
    network_condition += " || github.event_name == 'workflow_dispatch'"
    assert network_condition in workflow
    assert "-m network_replay --run-network" in workflow
    assert "-m network_live --run-network" not in workflow


def test_ci_fetches_full_history_for_issue_reference_guard() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    test_steps = workflow["jobs"]["test"]["steps"]
    checkout_steps = [
        step
        for step in test_steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert len(checkout_steps) == 1
    assert checkout_steps[0]["with"]["fetch-depth"] == 0

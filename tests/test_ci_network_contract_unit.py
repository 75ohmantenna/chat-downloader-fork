# SPDX-License-Identifier: MIT

"""Contract checks for GitHub Actions validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_runs_network_replay_on_schedule_dispatch_and_release_repo_pushes() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    # PyYAML interprets the unquoted workflow key `on` as a YAML 1.1 boolean.
    assert {"schedule", "workflow_dispatch", "push"} <= workflow[True].keys()
    assert workflow["jobs"]["test"]["if"] == "github.event_name != 'schedule'"
    replay_job = workflow["jobs"]["network-replay"]
    assert " ".join(replay_job["if"].split()) == (
        "github.event_name == 'schedule' || "
        "github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'push' && "
        "github.repository == '75ohmantenna/chat-downloader-fork')"
    )
    assert replay_job["steps"][-1]["run"] == (
        "uv run pytest -q -p no:rerunfailures -m network_replay --run-network"
    )


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


@pytest.mark.parametrize("job_name", ["test", "network-replay"])
def test_ci_uses_selected_interpreter_instead_of_local_python_pin(job_name) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    python_steps = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert len(python_steps) == 1
    assert python_steps[0]["id"] == "python"
    assert python_steps[0]["with"]["check-latest"] is True
    # uv prioritizes .python-version over PATH unless UV_PYTHON is explicit.
    run_steps = [step for step in steps if "run" in step]
    assert len(run_steps) == 2
    for step in run_steps:
        assert step["env"]["UV_PYTHON"] == "${{ steps.python.outputs.python-path }}"

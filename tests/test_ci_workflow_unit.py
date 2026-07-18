# SPDX-License-Identifier: MIT

"""Static contracts for the GitHub Actions workflow."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_checkout_fetches_full_history() -> None:
    """Keep the fork-history validation baseline available in hosted CI."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    checkout_step, _, _ = workflow.partition("      - uses: astral-sh/setup-uv@")

    assert "          fetch-depth: 0" in checkout_step

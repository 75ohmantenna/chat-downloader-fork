# SPDX-License-Identifier: MIT

"""Coverage configuration contract tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_project_coverage_does_not_broadly_exclude_ellipsis() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    exclusions = config["tool"]["coverage"]["report"]["exclude_also"]
    assert r"\.\.\." not in exclusions

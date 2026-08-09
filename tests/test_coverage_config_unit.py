# SPDX-License-Identifier: MIT

"""Coverage configuration contract tests."""

from __future__ import annotations

import re
from pathlib import Path

import coverage

ROOT = Path(__file__).resolve().parents[1]


def test_project_coverage_counts_ellipsis_bodies() -> None:
    configured_coverage = coverage.Coverage(
        config_file=ROOT / "pyproject.toml",
    )
    configured_coverage.load()

    exclusions = configured_coverage.get_exclude_list()
    assert not any(re.search(pattern, "...") for pattern in exclusions)
    assert any(re.search(pattern, "# pragma: no cover") for pattern in exclusions)
    assert any(re.search(pattern, "if TYPE_CHECKING:") for pattern in exclusions)

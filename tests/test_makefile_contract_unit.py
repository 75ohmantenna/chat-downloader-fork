# SPDX-License-Identifier: MIT

"""Contract tests for Makefile validation targets."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_make_ci_target_runs_canonical_validation_steps_in_order() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "ci: lock-check lint fmt-check typecheck coverage smoke" in makefile


def test_make_lint_includes_import_linter_contracts() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "$(UV_RUN) ruff check src/chat_downloader tests" in makefile
    assert "$(UV_RUN) lint-imports" in makefile


def test_make_test_paths_exclude_network_tests() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert '$(UV) run pytest -q -p no:rerunfailures -m "not network"' in makefile
    assert (
        '$(UV_RUN) coverage run -m pytest -q -p no:rerunfailures -m "not network"'
        in makefile
    )


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="compatible GNU Make/POSIX toolchain required",
)
def test_make_clean_removes_all_generated_project_artifacts(tmp_path: Path) -> None:
    project_copy = tmp_path / "project"
    project_copy.mkdir()
    shutil.copy2(ROOT / "Makefile", project_copy / "Makefile")
    generated_paths = [
        project_copy / ".import_linter_cache",
        project_copy / ".mypy_cache",
        project_copy / ".pytest_cache",
        project_copy / ".ruff_cache",
        project_copy / "build",
        project_copy / "dist",
        project_copy / "htmlcov",
        project_copy / "package.egg-info",
        project_copy / "src" / "chat_downloader.egg-info",
        project_copy / "src" / "chat_downloader" / "__pycache__",
    ]
    for path in generated_paths:
        path.mkdir(parents=True)
        (path / "generated").write_text("generated", encoding="utf-8")
    coverage_file = project_copy / ".coverage"
    coverage_file.write_text("generated", encoding="utf-8")

    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - resolved local make executable
        [make, "clean"],
        cwd=project_copy,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not coverage_file.exists()
    assert all(not path.exists() for path in generated_paths)

# SPDX-License-Identifier: MIT

"""Static contract tests for Makefile validation targets."""

from __future__ import annotations

from pathlib import Path

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

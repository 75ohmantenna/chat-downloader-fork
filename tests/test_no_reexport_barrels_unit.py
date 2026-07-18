# SPDX-License-Identifier: MIT

"""Guard against re-export-only "barrel" modules.

A module whose body is nothing but imports plus ``__all__`` adds an import
boundary without any cohesive behavior — the kind of shim the decomposition
policy (see AGENTS.md) calls a split on the wrong axis. ``__init__.py`` package
facades are exempt; genuine single-purpose facades can be allowlisted with a
rationale.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "chat_downloader"

# Non-__init__ modules that are intentionally thin facades. Add with a reason.
ALLOWLIST: frozenset[str] = frozenset()

# ``__main__.py`` is a conventional CLI entry point, not a re-export barrel.
_EXEMPT_NAMES = frozenset({"__init__.py", "__main__.py"})


def _is_reexport_barrel(tree: ast.Module) -> bool:
    """True when a module only imports names and (optionally) declares __all__.

    A barrel has import statements, defines nothing (no class/def), and carries
    no assignments other than ``__all__``. Constant/data modules (which assign
    real values) and modules with any class/function are not barrels.
    """
    has_import = False
    has_definition = False
    has_non_all_assignment = False

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            has_import = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            has_definition = True
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets != {"__all__"}:
                has_non_all_assignment = True
        elif isinstance(node, ast.AnnAssign) and not (
            isinstance(node.target, ast.Name) and node.target.id == "__all__"
        ):
            has_non_all_assignment = True
        elif isinstance(node, (ast.Expr, ast.ImportFrom)):
            # Module docstring / future import — ignore.
            continue

    return has_import and not has_definition and not has_non_all_assignment


def test_no_reexport_only_barrels() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name in _EXEMPT_NAMES:
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _is_reexport_barrel(tree):
            offenders.append(rel)

    assert not offenders, (
        "re-export-only barrel modules (fold into the caller or make them "
        "cohesive; see AGENTS.md decomposition policy):\n" + "\n".join(offenders)
    )

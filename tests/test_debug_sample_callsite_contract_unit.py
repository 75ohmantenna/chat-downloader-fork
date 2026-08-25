# SPDX-License-Identifier: MIT

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "chat_downloader"


def test_production_debug_sample_calls_are_bounded() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if function_name != "capture_debug_sample":
                continue
            if any(keyword.arg == "sample_limit" for keyword in node.keywords):
                continue
            offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")

    assert not offenders, "unbounded debug sample calls:\n" + "\n".join(offenders)

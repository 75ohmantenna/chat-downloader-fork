# SPDX-License-Identifier: MIT

"""Contract: docs/architecture.md lists every top-level source module.

The ``### Top-level`` table in ``docs/architecture.md`` claims to enumerate the
top-level modules of ``src/chat_downloader``. This ratchet keeps that claim
honest: a new or renamed top-level module must be added to the table in the same
change, mechanically enforcing the doc-sync policy in ``AGENTS.md``.

Scope is deliberately limited to top-level modules. The doc intentionally
summarizes the deeper tree (e.g. one ``parsing/`` row), so it is not enumerated
here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chat_downloader"
ARCHITECTURE_DOC = ROOT / "docs" / "architecture.md"


def _top_level_section() -> str:
    """Return the ``### Top-level`` section text, up to the next ``### `` heading."""
    text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"^### Top-level\b.*?(?=^### )",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "architecture.md is missing a '### Top-level' section"
    return match.group(0)


def test_architecture_doc_lists_all_top_level_modules() -> None:
    section = _top_level_section()
    offenders = [
        path.name
        for path in sorted(SRC.glob("*.py"))
        if not path.name.startswith("__") and f"`{path.name}`" not in section
    ]
    assert not offenders, (
        "top-level modules missing from the architecture.md Top-level table:\n"
        + "\n".join(offenders)
    )

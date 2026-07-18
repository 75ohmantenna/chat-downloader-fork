# SPDX-License-Identifier: MIT

"""Contracts for local links and the hand-written Python API reference."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from urllib.parse import unquote

from chat_downloader import __all__ as public_exports
from chat_downloader.runtime import RunResult

ROOT = Path(__file__).resolve().parents[1]
API_REFERENCE = ROOT / "docs" / "python-api-reference.md"
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def _project_documents() -> list[Path]:
    """Return human-authored Markdown documents owned by this repository."""
    return sorted(
        {
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            *(ROOT / "src").rglob("AGENTS.md"),
        }
    )


def test_local_markdown_links_resolve() -> None:
    """Reject broken relative links in project-owned Markdown."""
    broken: list[str] = []

    for document in _project_documents():
        text = document.read_text(encoding="utf-8")
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative and not (document.parent / relative).exists():
                doc_name = document.relative_to(ROOT).as_posix()
                broken.append(f"{doc_name}: {target}")

    assert not broken, "broken local Markdown links:\n" + "\n".join(broken)


def test_python_api_reference_lists_exact_top_level_exports() -> None:
    """Keep the documented import block aligned with package ``__all__``."""
    text = API_REFERENCE.read_text(encoding="utf-8")
    match = re.search(
        r"from chat_downloader import \(\n(?P<body>.*?)\n\)",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "Python API reference is missing its import block"
    documented = {
        line.strip().removesuffix(",")
        for line in match.group("body").splitlines()
        if line.strip()
    }
    assert documented == set(public_exports)


def test_python_api_reference_documents_run_result_fields() -> None:
    """Keep the observable ``run()`` result shape documented."""
    text = API_REFERENCE.read_text(encoding="utf-8")
    for field in fields(RunResult):
        assert f"`{field.name}`" in text

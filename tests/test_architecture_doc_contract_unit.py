# SPDX-License-Identifier: MIT

"""Contract: the architecture guide inventories immediate source modules."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chat_downloader"
ARCHITECTURE_DOC = ROOT / "docs" / "architecture.md"

INVENTORIES: dict[str, Path] = {
    "### Top-level": SRC,
    "### `models/`": SRC / "models",
    "### `runtime/`": SRC / "runtime",
    "### `output/`": SRC / "output",
    "### `sites/` (shared)": SRC / "sites",
    "### `sites/youtube/`": SRC / "sites" / "youtube",
    "### `sites/twitch/`": SRC / "sites" / "twitch",
    "### `sites/kick/`": SRC / "sites" / "kick",
}


def _section(text: str, heading: str) -> str:
    """Return a level-three architecture section through the next peer."""
    match = re.search(
        rf"^{re.escape(heading)}\s*$.*?(?=^###\s|\Z)",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, f"architecture.md is missing {heading!r}"
    return match.group(0)


def test_architecture_doc_lists_immediate_modules() -> None:
    """Require every immediate non-package module in its package section."""
    text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    offenders: list[str] = []

    for heading, directory in INVENTORIES.items():
        section = _section(text, heading)
        for path in sorted(directory.glob("*.py")):
            if path.name == "__init__.py":
                continue
            if f"`{path.name}`" not in section:
                rel = path.relative_to(SRC).as_posix()
                offenders.append(f"{heading}: {rel}")

    assert not offenders, (
        "source modules missing from architecture.md inventories:\n"
        + "\n".join(offenders)
    )

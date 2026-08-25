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
    "### `formatting/`": SRC / "formatting",
    "### `utils/`": SRC / "utils",
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


def test_architecture_doc_lists_exact_immediate_modules() -> None:
    """Keep every package inventory exact, including removal of stale names."""
    text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    offenders: list[str] = []

    for heading, directory in INVENTORIES.items():
        section = _section(text, heading)
        expected = {
            path.name for path in directory.glob("*.py") if path.name != "__init__.py"
        }
        documented = {
            module_name
            for module_name in re.findall(r"`([^`]+\.py)`", section)
            if "/" not in module_name
            and "*" not in module_name
            and module_name != "__init__.py"
        }
        missing = sorted(expected - documented)
        stale = sorted(documented - expected)
        if missing:
            offenders.append(f"{heading}: missing {', '.join(missing)}")
        if stale:
            offenders.append(f"{heading}: stale {', '.join(stale)}")

    assert not offenders, "architecture.md package inventory drift:\n" + "\n".join(
        offenders
    )

# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from pathlib import Path

from chat_downloader.metadata import __version__

_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$", re.MULTILINE)
_REPO_ROOT = Path(__file__).parent.parent


def test_changelog_version_matches_package_version() -> None:
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = _HEADING.findall(changelog)
    assert versions, "CHANGELOG.md contains no numbered release headings"
    latest = versions[0]
    assert latest == __version__, (
        f"CHANGELOG.md latest release is {latest!r} but "
        f"metadata.__version__ is {__version__!r}. "
        "Update CHANGELOG.md in the same commit as every version bump."
    )

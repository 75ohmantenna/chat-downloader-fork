# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from pathlib import Path

from chat_downloader.metadata import __url__, __version__

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


def test_readme_install_commands_pin_package_release() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    refs = re.findall(rf"git\+{re.escape(__url__)}\.git([^\s`]*)", readme)

    assert refs == [f"@v{__version__}"] * 4

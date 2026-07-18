# SPDX-License-Identifier: MIT

"""Reject fork-history messages that can notify upstream issue trackers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORK_HISTORY_BASE = "809699b92100fa9eec0940a82edc9bc1ce0f3d55"
_ISSUE_REFERENCE = re.compile(r"(?<![\w/])#[0-9]+\b")
_UPSTREAM_ISSUE_URL = "github.com/" + "xenova/chat-downloader/" + "issues/"


def main() -> int:
    """Return nonzero when reachable fork history can reference an issue."""
    git = shutil.which("git")
    if git is None:
        print("Git is required to check fork history")
        return 2
    result = subprocess.run(  # noqa: S603 - fixed local Git command
        [
            git,
            "log",
            f"{FORK_HISTORY_BASE}..HEAD",
            "--format=%H%x1f%B%x1e",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    violations: list[str] = []
    for raw_record in result.stdout.split("\x1e"):
        record = raw_record.strip()
        if not record:
            continue
        commit, _, message = record.partition("\x1f")
        if _ISSUE_REFERENCE.search(message) or _UPSTREAM_ISSUE_URL in message:
            subject = message.splitlines()[0] if message else "<empty>"
            violations.append(f"{commit[:12]} {subject}")

    if violations:
        print("issue references found in fork history:")
        print("\n".join(violations))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

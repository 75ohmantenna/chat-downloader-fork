# SPDX-License-Identifier: MIT

"""Behavioral contracts for the standalone fork-history checker."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_issue_references.py"
_RUN_CHECKER = (
    "import locale, pathlib, runpy, sys; "
    "assert sys.flags.utf8_mode == 0; "
    "assert locale.getpreferredencoding(False).lower() in "
    "('ascii', 'us-ascii', 'ansi_x3.4-1968'); "
    "namespace = runpy.run_path(sys.argv[1]); "
    "namespace['main'].__globals__.update("
    "ROOT=pathlib.Path(sys.argv[2]), FORK_HISTORY_BASE=sys.argv[3]); "
    "raise SystemExit(namespace['main']())"
)


def _git(
    git: str,
    repo: Path,
    env: dict[str, str],
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    """Run real Git against only a disposable repository and private config."""
    result = subprocess.run(  # noqa: S603 - resolved local Git and test-owned arguments
        [git, *args],
        cwd=repo,
        env=env,
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout.strip()


@pytest.fixture
def history(tmp_path: Path) -> tuple[str, Path, dict[str, str], str]:
    """Create an isolated baseline without checkout files, hooks, or signing."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required for synthetic history")
    repo = tmp_path / "history"
    repo.mkdir()
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="History Test",
        GIT_AUTHOR_EMAIL="history@example.invalid",
        GIT_COMMITTER_NAME="History Test",
        GIT_COMMITTER_EMAIL="history@example.invalid",
        GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
        GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000",
        LC_ALL="C",
        LANG="C",
        PYTHONUTF8="0",
        PYTHONCOERCECLOCALE="0",
        PYTHONIOENCODING="ascii:strict",
    )
    _git(git, repo, env, "init", "--quiet", "--template=")
    _git(git, repo, env, "config", "i18n.logOutputEncoding", "ISO-8859-1")
    tree = _git(
        git, repo, env, "hash-object", "-t", "tree", "-w", "--stdin", input_bytes=b""
    )
    baseline = _git(
        git, repo, env, "commit-tree", tree.decode("ascii"), input_bytes=b"Baseline\n"
    ).decode("ascii")
    return git, repo, env, baseline


def _run_checker(
    repo: Path, env: dict[str, str], baseline: str
) -> subprocess.CompletedProcess[bytes]:
    """Execute the checker in a real ASCII-locale Python subprocess."""
    return subprocess.run(  # noqa: S603 - fixed interpreter and test-owned local paths
        [sys.executable, "-c", _RUN_CHECKER, str(CHECKER), str(repo), baseline],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX C locale required")
@pytest.mark.parametrize(
    ("message", "output_encoding", "expected_code", "expected_subject"),
    [
        ("Maintain café history", "ascii:strict", 0, b""),
        *[
            (
                "Fix café " + "#" + "123",
                "ascii:" + policy,
                1,
                "Fix café #123".encode("ascii", errors="backslashreplace"),
            )
            for policy in ("strict", "ignore", "replace")
        ],
        ("Fix café " + "#" + "123", "utf-8:strict", 1, "Fix café #123".encode()),
        (
            "Fix 日本😀 " + "#" + "123",
            "iso2022_jp:strict",
            1,
            "Fix 日本\\U0001f600 #123".encode("iso2022_jp"),
        ),
    ],
)
def test_history_encoding_is_independent_of_locale_and_git_config(
    history: tuple[str, Path, dict[str, str], str],
    message: str,
    output_encoding: str,
    expected_code: int,
    expected_subject: bytes,
) -> None:
    git, repo, env, baseline = history
    env["PYTHONIOENCODING"] = output_encoding
    tree = _git(git, repo, env, "rev-parse", baseline + "^{tree}").decode("ascii")
    commit = _git(
        git,
        repo,
        env,
        "commit-tree",
        tree,
        "-p",
        baseline,
        input_bytes=(message + "\n").encode("utf-8"),
    ).decode("ascii")
    _git(git, repo, env, "update-ref", "HEAD", commit)

    result = _run_checker(repo, env, baseline)

    assert result.returncode == expected_code, result.stderr
    assert result.stderr == b""
    if expected_code:
        assert result.stdout == (
            b"issue references found in fork history:\n"
            + commit[:12].encode("ascii")
            + b" "
            + expected_subject
            + b"\n"
        )
    else:
        assert result.stdout == b""


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture required")
@pytest.mark.parametrize("message", [b"Malformed \xff", b"Malformed \xff #123"])
def test_malformed_history_fails_without_traceback(
    tmp_path: Path, message: bytes
) -> None:
    """Reject invalid bytes even when they would otherwise hide a reference."""
    git = tmp_path / "git"
    payload = b"a" * 40 + b"\x1f" + message + b"\x1e\n"
    git.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write({payload!r})\n",
        encoding="ascii",
    )
    git.chmod(0o700)
    env = dict(
        os.environ,
        PATH=str(tmp_path),
        LC_ALL="C",
        LANG="C",
        PYTHONUTF8="0",
        PYTHONCOERCECLOCALE="0",
        PYTHONIOENCODING="ascii:strict",
    )

    result = _run_checker(tmp_path, env, "unused")

    assert result.returncode == 2
    assert result.stdout == b"Git history output is not valid UTF-8\n"
    assert result.stderr == b""

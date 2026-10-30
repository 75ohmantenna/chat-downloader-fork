# SPDX-License-Identifier: MIT

"""Opt-in Kick CLI smoke matrix with private artifacts and aggregate results."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _seconds(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 86400:
        msg = "Use seconds greater than zero, up to 86400."
        raise argparse.ArgumentTypeError(msg)
    return result


def _cases(duration: float) -> list[tuple[str, list[str], bool]]:
    return [
        ("help", ["--help"], True),
        ("version", ["--version"], True),
        ("capture", ["--message_groups", "all", "--timeout", str(duration)], True),
        ("quiet", ["--quiet", "--max_messages", "5"], True),
        ("compact", ["--message_groups", "all", "--compact_console"], True),
        ("filtered", ["--message_types", "text_message", "--sort_keys", "true"], True),
        (
            "headers",
            ["--header", "Accept-Language: en", "--user-agent", "Mozilla/5.0"],
            True,
        ),
        (
            "inactivity",
            ["--message_types", "goal_canceled", "--inactivity_timeout", "5"],
            True,
        ),
        ("invalid_extension", ["--output", "invalid.csv"], False),
        (
            "live_resume",
            ["--output", "chat.jsonl", "--resume", "checkpoint.json"],
            False,
        ),
        ("require_complete", ["--require_complete"], False),
    ]


def _aggregate_fields(manifest: object) -> dict[str, object]:
    """Only expose validated counts and fixed status vocabularies."""
    msg = "Invalid aggregate manifest fields."
    if not isinstance(manifest, dict):
        raise TypeError(msg)
    count = manifest["message_count"]
    parity = manifest["parity_status"]
    status = manifest["provider_inspection"]["status"]
    if (
        type(count) is not int
        or count < 0
        or parity not in {"passed", "failed", "not_run", "not_requested"}
        or status not in {"ok", "review", "error"}
    ):
        raise ValueError(msg)
    return {"records": count, "parity_status": parity, "inspection_status": status}


def _run_case(
    directory: Path,
    name: str,
    arguments: list[str],
    *,
    expected_success: bool,
    timeout: float,
) -> dict[str, object]:
    """Persist child output privately; never return exception or provider text."""
    with (
        (directory / "stdout.log").open("xb") as stdout,
        (directory / "stderr.log").open("xb") as stderr,
    ):
        try:
            completed = subprocess.run(  # noqa: S603 - fixed interpreter and module
                [sys.executable, "-m", "chat_downloader", *arguments],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"case": name, "passed": False, "error": "process_timeout"}
    passed = completed.returncode == (0 if expected_success else 1)
    result: dict[str, object] = {
        "case": name,
        "passed": passed,
        "exit_code": completed.returncode,
    }
    if expected_success and name not in {"help", "version"}:
        try:
            manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
            result.update(_aggregate_fields(manifest))
            inspection = manifest["provider_inspection"]
            result["passed"] = (
                passed
                and manifest["success"] is True
                and manifest["parity_status"] == "passed"
                and inspection["status"] == "ok"
                and (name == "inactivity" or manifest["message_count"] > 0)
                and (name != "quiet" or (directory / "stdout.log").stat().st_size == 0)
            )
        except (OSError, ValueError, KeyError, TypeError):
            result.update(passed=False, error="invalid_or_missing_manifest")
    elif not expected_success:
        expected = {
            "invalid_extension": "Unsupported output format 'csv'.",
            "live_resume": "Resume requires a completed replay with stable identity.",
            "require_complete": "Complete capture requires a completed replay.",
        }[name]
        log = (directory / "stderr.log").read_text("utf-8", errors="replace")
        result["passed"] = passed and expected in log and "Traceback (" not in log
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run explicitly authorized network cases and print aggregate results."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--run-network", action="store_true", required=True)
    parser.add_argument("--channel", default="xqc")
    parser.add_argument("--duration", type=_seconds, default=480.0)
    parser.add_argument("--case-duration", type=_seconds, default=10.0)
    parser.add_argument("--proxy", action="append", default=[])
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.channel) is None:
        parser.error("Use a Kick channel slug.")
    directory = args.artifacts.absolute()
    # Exclusive creation prevents accidental replacement of existing captures.
    directory.mkdir(mode=0o700)
    url = f"https://kick.com/{args.channel}"
    results = []
    for name, options, success in _cases(args.duration):
        case_dir = directory / name
        case_dir.mkdir()
        capture_options = (
            [
                "--output",
                "chat.jsonl",
                "--output",
                "chat.txt",
                "--verify_output",
                "--run_manifest",
                "manifest.json",
            ]
            if success and name not in {"help", "version"}
            else []
        )
        results.append(
            _run_case(
                case_dir,
                name,
                [
                    url,
                    "--timeout",
                    str(args.case_duration),
                    "--max_attempts",
                    "2",
                    "--retry_timeout",
                    "0",
                    "--logging",
                    "debug",
                    *capture_options,
                    *options,
                ],
                expected_success=success,
                timeout=max(
                    args.duration if name == "capture" else 0, args.case_duration
                )
                + 45,
            )
        )
    for index, proxy in enumerate(args.proxy):
        name = f"proxy_{index + 1}"
        case_dir = directory / name
        case_dir.mkdir()
        results.append(
            _run_case(
                case_dir,
                name,
                [
                    url,
                    "--proxy",
                    proxy,
                    "--timeout",
                    str(args.case_duration),
                    "--max_attempts",
                    "1",
                    "--connect_timeout",
                    "10",
                    "--read_timeout",
                    "10",
                    "--message_groups",
                    "all",
                    "--output",
                    "chat.jsonl",
                    "--output",
                    "chat.txt",
                    "--verify_output",
                    "--run_manifest",
                    "manifest.json",
                ],
                expected_success=True,
                timeout=args.case_duration + 45,
            )
        )
    report = {"passed": all(result["passed"] for result in results), "cases": results}
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: MIT

"""Opt-in Kick CLI smoke matrix with private artifacts and aggregate results."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.metadata import __program__, __version__
from chat_downloader.utils.atomic_files import atomic_text_file
from chat_downloader.utils.capture_reader import open_capture_input, read_capture_lines

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
        (
            "realtime",
            ["--message_groups", "messages", "--timeout", str(duration)],
            True,
        ),
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


def _realtime_fields(manifest: dict[str, object]) -> dict[str, object]:
    """Prove recorded socket delivery conservatively from frozen accounting."""
    msg = "Invalid realtime accounting."
    diagnostics = manifest["provider_diagnostics"]
    inspection = manifest["provider_inspection"]
    if not isinstance(diagnostics, dict) or not isinstance(inspection, dict):
        raise TypeError(msg)
    accounting = inspection["frame_accounting"]
    if not isinstance(accounting, dict):
        raise TypeError(msg)
    keys = (
        "websocket_frame_count",
        "control_frame_count",
        "parsed_event_count",
        "unsupported_event_count",
        "malformed_event_count",
        "pusher_error_count",
        "pusher_connection_count",
        "centrifugo_connection_count",
        "public_subscription_count",
        "synthetic_frame_count",
        "preloaded_emitted_count",
        "startup_backfill_emitted_count",
        "live_emitted_count",
        "reconnect_backfill_emitted_count",
    )
    counts = {key: diagnostics[key] for key in keys}
    prefetched = manifest["prefetched_after_deadline_count"]
    records = inspection["records"]
    if (
        any(type(value) is not int or value < 0 for value in counts.values())
        or type(prefetched) is not int
        or prefetched < 0
        or type(records) is not int
        or records != manifest["message_count"]
        or manifest["deadline_prefetch_count_complete"] is not True
        or any(
            type(accounting[key]) is not int or accounting[key] != value
            for key, value in counts.items()
        )
        or any(
            type(accounting[key]) is not int or accounting[key] != 0
            for key in (
                "unaccounted_frames",
                "emitted_minus_records",
                "summary_minus_records",
                "malformed_type_count_gap",
                "message_type_count_mismatches",
                "deadline_accounting_incomplete",
                "run_failed",
            )
        )
    ):
        raise ValueError(msg)
    if (
        counts["websocket_frame_count"] + counts["synthetic_frame_count"]
        != sum(
            counts[key]
            for key in (
                "control_frame_count",
                "parsed_event_count",
                "unsupported_event_count",
                "malformed_event_count",
                "pusher_error_count",
            )
        )
        or sum(
            counts[key]
            for key in (
                "preloaded_emitted_count",
                "startup_backfill_emitted_count",
                "live_emitted_count",
                "reconnect_backfill_emitted_count",
            )
        )
        != records + prefetched
        or counts["live_emitted_count"] > counts["parsed_event_count"]
        or counts["public_subscription_count"] > counts["control_frame_count"]
        or counts["control_frame_count"] > counts["websocket_frame_count"]
    ):
        raise ValueError(msg)
    # Each REST frame can emit at most one live record. Subtract all of them,
    # plus every discarded deadline prefetch, even when they were not emitted.
    socket_records_min = max(
        0, counts["live_emitted_count"] - counts["synthetic_frame_count"] - prefetched
    )
    return {
        "realtime_delivery": (
            counts["public_subscription_count"] > 0
            and counts["pusher_connection_count"]
            + counts["centrifugo_connection_count"]
            > 0
            and socket_records_min > 0
        ),
        "confirmed_subscriptions": counts["public_subscription_count"],
        "socket_records_min": socket_records_min,
    }


class _InvalidCaptureError(ValueError):
    """The case's JSONL capture could not be validated."""


def _capture_matches_filter(
    capture: Path,
    count: int,
    message_type: str,
    reported_types: object,
    *,
    sorted_keys: bool = False,
) -> bool:
    expected_types = {message_type: count} if count else {}
    if (
        not isinstance(reported_types, dict)
        or reported_types != expected_types
        or any(type(value) is not int for value in reported_types.values())
    ):
        return False
    records = 0
    try:
        with closing(read_capture_lines(capture)) as lines:
            for line in lines:
                record = line.record
                if record is None:
                    raise _InvalidCaptureError
                if record.get("message_type") != message_type:
                    return False
                if sorted_keys and json.dumps(record) != json.dumps(
                    record, sort_keys=True
                ):
                    return False
                records += 1
    except FileNotFoundError as error:
        # Lazy output writers do not create a file for a zero-record run.
        if count == 0:
            return True
        raise _InvalidCaptureError from error
    except (OSError, RecursionError) as error:
        raise _InvalidCaptureError from error
    return records == count


def _capture_case_passed(
    directory: Path, name: str, manifest: dict[str, object]
) -> bool:
    count = manifest["message_count"]
    if type(count) is not int:
        return False
    if name == "quiet":
        reason = manifest["termination_reason"]
        return (
            count <= 5
            and (reason == "timeout" or (reason == "message_limit" and count == 5))
            and (directory / "stdout.log").stat().st_size == 0
        )
    if name == "inactivity":
        return manifest["termination_reason"] == "inactivity_timeout" and (
            _capture_matches_filter(
                directory / "chat.jsonl",
                count,
                "goal_canceled",
                manifest["message_type_counts"],
            )
        )
    if name == "filtered":
        return count > 0 and _capture_matches_filter(
            directory / "chat.jsonl",
            count,
            "text_message",
            manifest["message_type_counts"],
            sorted_keys=True,
        )
    return count > 0


def _cli_info_matches(stdout: Path, name: str) -> bool:
    output = stdout.read_text("utf-8", errors="replace")
    if name == "version":
        return output == f"{__version__}\n"
    options = set(re.findall(r"(?<![\w-])--[\w-]+", output))
    return (
        output.startswith(f"usage: {__program__} ")
        and {
            "--help",
            "--version",
            "--message_types",
            "--inactivity_timeout",
        }
        <= options
    )


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
    if name in {"help", "version"}:
        result["passed"] = passed and _cli_info_matches(directory / "stdout.log", name)
    elif expected_success:
        try:
            with open_capture_input(directory / "manifest.json") as source:
                manifest = json.loads(source.read().decode("utf-8"))
            result.update(_aggregate_fields(manifest))
            inspection = manifest["provider_inspection"]
            result["passed"] = (
                passed
                and manifest["success"] is True
                and manifest["parity_status"] == "passed"
                and inspection["status"] == "ok"
                and _capture_case_passed(directory, name, manifest)
            )
            if name == "realtime":
                result.update(_realtime_fields(manifest))
                result["passed"] = result["passed"] and result["realtime_delivery"]
        except _InvalidCaptureError:
            result.update(passed=False, error="invalid_or_missing_capture")
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            result.update(passed=False, error="invalid_or_missing_manifest")
    else:
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
    parser.add_argument(
        "--channel",
        default=os.environ.get("KICK_TEST_CHANNEL") or None,
        help="Active public Kick slug (or KICK_TEST_CHANNEL).",
    )
    parser.add_argument("--duration", type=_seconds, default=480.0)
    parser.add_argument("--case-duration", type=_seconds, default=10.0)
    parser.add_argument("--proxy", action="append", default=[])
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    channel = args.channel
    if channel is None:
        parser.error(
            "Select an active public Kick channel with --channel or KICK_TEST_CHANNEL."
        )
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", channel) is None:
        parser.error("Use a Kick channel slug.")
    directory = args.artifacts.absolute()
    # Exclusive creation prevents accidental replacement of existing captures.
    directory.mkdir(mode=0o700)
    url = f"https://kick.com/{channel}"
    results = []
    for name, options, success in _cases(args.duration):
        case_dir = directory / name
        case_dir.mkdir()
        # Leave the five-second inactivity timer time to fire before the deadline.
        case_duration = (
            max(args.case_duration, 6.0) if name == "inactivity" else args.case_duration
        )
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
                    str(case_duration),
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
                    args.duration if name in {"capture", "realtime"} else 0,
                    case_duration,
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
    try:
        print(json.dumps(report, sort_keys=True), flush=True)
    finally:
        with atomic_text_file(directory / "report.json") as target:
            json.dump(report, target, indent=2)
            target.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

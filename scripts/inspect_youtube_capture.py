# SPDX-License-Identifier: MIT

"""Inspect one YouTube JSONL capture without printing message content."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import NoReturn

from chat_downloader.sites.youtube.capture_inspection import inspect_capture
from chat_downloader.utils.capture_reader import open_capture_input

_LIMIT = 1024 * 1024
_INVALID_MANIFEST = "invalid_manifest"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - never echo input
        print('{"error": "invalid_arguments"}')
        raise SystemExit(2)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_INVALID_MANIFEST)
        result[key] = value
    return result


def _read_manifest(path: Path) -> dict[str, object]:
    with open_capture_input(path) as source:
        payload = source.read(_LIMIT + 1)
    if len(payload) > _LIMIT:
        raise ValueError(_INVALID_MANIFEST)
    summary = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(summary, dict):
        raise TypeError(_INVALID_MANIFEST)
    return summary


def main(argv: list[str] | None = None) -> int:
    """Print aggregate findings; exit 1 for review, or 2 for invalid input."""
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = None
        if args.manifest is not None:
            summary = _read_manifest(args.manifest)
        report = inspect_capture(args.jsonl, run_summary=summary)
    except (OSError, sqlite3.Error):
        print('{"error": "input_or_temporary_storage_io"}')
        return 2
    except (ValueError, TypeError, RecursionError):
        print('{"error": "invalid_manifest"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "review" else 0


if __name__ == "__main__":
    raise SystemExit(main())

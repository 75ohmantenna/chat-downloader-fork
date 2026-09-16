# SPDX-License-Identifier: MIT

"""Content-free, offline inspection of one Twitch live JSONL capture."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import NoReturn

from chat_downloader.sites.twitch.capture_inspection import inspect_capture


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - never echo input
        print('{"error": "invalid_arguments"}')
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    """Print only aggregate findings; use exit 1 for review and 2 for input errors."""
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--debug-log", type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_capture(args.jsonl, args.debug_log)
    except (OSError, sqlite3.Error):
        print('{"error": "input_or_temporary_storage_io"}')
        return 2
    except (ValueError, TypeError, SyntaxError, RecursionError):
        print('{"error": "invalid_run_summary"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "review" else 0


if __name__ == "__main__":
    raise SystemExit(main())

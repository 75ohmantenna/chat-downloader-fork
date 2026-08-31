# SPDX-License-Identifier: MIT

"""Privacy-safe CLI for offline JSONL/TXT capture parity audits."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NoReturn

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.debugging import disable_logger
from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import (
    AuditStats,
    InputIdentityError,
    audit_capture,
)

EXIT_OK = 0
EXIT_AUDIT_FAILED = 1
EXIT_USAGE_OR_IO = 2


class _ContentFreeArgumentParser(argparse.ArgumentParser):
    """Argument parser whose failures never repeat untrusted argv values."""

    def error(self, message: str) -> NoReturn:  # noqa: ARG002 - argparse override
        print("ERROR kind=invalid_arguments")
        raise SystemExit(EXIT_USAGE_OR_IO)


def _format_index(value: int | None) -> str:
    """Render an optional one-based line index for the summary."""
    return "-" if value is None else str(value)


def _format_trailing_newline(*, value: bool | None) -> str:
    """Render final-newline state for an empty or nonempty artifact."""
    if value is None:
        return "empty"
    return "yes" if value else "no"


def _format_newline_style(
    style: str | None,
    *,
    mixed_count: int,
) -> str:
    """Render the observed physical newline style."""
    if mixed_count:
        return "mixed"
    return style or "none"


def _format_summary(stats: AuditStats) -> str:
    """Build a concise summary that cannot disclose captured chat text."""
    jsonl_trailing = _format_trailing_newline(value=stats.jsonl_trailing_newline)
    txt_trailing = _format_trailing_newline(value=stats.txt_trailing_newline)
    jsonl_newline = _format_newline_style(
        stats.jsonl_newline_style,
        mixed_count=stats.jsonl_mixed_newlines,
    )
    txt_newline = _format_newline_style(
        stats.txt_newline_style,
        mixed_count=stats.txt_mixed_newlines,
    )
    fields = [
        "PASS" if not stats.failed else "FAIL",
        f"jsonl_lines={stats.jsonl_lines}",
        f"jsonl_records={stats.jsonl_records}",
        f"expected_txt_lines={stats.expected_txt_lines}",
        f"txt_lines={stats.txt_lines}",
        f"suppressed_duplicates={stats.suppressed_duplicates}",
        f"jsonl_errors={stats.jsonl_errors}",
        f"dedup_errors={stats.dedup_errors}",
        f"render_errors={stats.render_errors}",
        f"txt_utf8_errors={stats.txt_utf8_errors}",
        f"text_mismatches={stats.text_mismatches}",
        f"newline_errors={stats.newline_errors}",
        f"count_mismatch={'yes' if stats.count_mismatch else 'no'}",
        f"jsonl_trailing_newline={jsonl_trailing}",
        f"txt_trailing_newline={txt_trailing}",
        f"jsonl_newline_style={jsonl_newline}",
        f"txt_newline_style={txt_newline}",
        f"newline_style_mismatch={'yes' if stats.newline_style_mismatch else 'no'}",
        f"comparison_complete={'yes' if stats.comparison_complete else 'no'}",
        f"comparison_skipped={stats.comparison_skipped}",
        f"dedup_resets_applied={stats.dedup_resets_applied}",
        f"dedup_reset_errors={stats.dedup_reset_errors}",
        f"jsonl_missing={'yes' if stats.jsonl_missing else 'no'}",
        f"txt_missing={'yes' if stats.txt_missing else 'no'}",
    ]
    if stats.failed:
        fields.extend(
            (
                f"first_issue={stats.first_issue or '-'}",
                (
                    "first_issue_jsonl_line="
                    f"{_format_index(stats.first_issue_jsonl_line)}"
                ),
                f"first_issue_txt_line={_format_index(stats.first_issue_txt_line)}",
                (
                    "first_mismatch_jsonl_line="
                    f"{_format_index(stats.first_mismatch_jsonl_line)}"
                ),
                (
                    "first_mismatch_txt_line="
                    f"{_format_index(stats.first_mismatch_txt_line)}"
                ),
            )
        )
    return " ".join(fields)


def _nonnegative_int(value: str) -> int:
    """Parse an integer greater than or equal to zero for argparse."""
    parsed = int(value)
    if parsed < 0:
        msg = "value must be nonnegative"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _positive_int(value: str) -> int:
    """Parse an integer greater than zero for argparse."""
    parsed = int(value)
    if parsed <= 0:
        msg = "value must be positive"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """Create the artifact-audit argument parser."""
    parser = _ContentFreeArgumentParser(
        allow_abbrev=False,
        description=(
            "Audit exact parity between a raw JSONL capture and its formatted "
            "TXT capture without printing captured messages."
        ),
    )
    parser.add_argument("jsonl_path", type=Path, help="raw JSONL capture path")
    parser.add_argument("txt_path", type=Path, help="formatted TXT capture path")
    parser.add_argument(
        "--format",
        dest="format_name",
        required=True,
        help=(
            "resolved production format name, for example twitch, kick, or "
            "youtube_live_default"
        ),
    )
    parser.add_argument(
        "--format-file",
        type=Path,
        help="optional custom formatter JSON used for the capture",
    )
    parser.add_argument(
        "--max-seen-message-ids",
        type=_nonnegative_int,
        default=DEFAULT_MAX_SEEN_MESSAGE_IDS,
        help=(
            "formatted dedup cache limit used by the capture; zero uses the "
            "production default"
        ),
    )
    parser.add_argument(
        "--dedup-reset-before-jsonl-line",
        dest="dedup_reset_before_lines",
        action="append",
        type=_positive_int,
        default=[],
        metavar="LINE",
        help=(
            "reset formatted dedup state before this one-based JSONL line; "
            "repeat in strictly increasing order for appended logical runs"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the capture audit and return its stable process exit code."""
    args = _build_parser().parse_args(argv)
    # The production deduplicator can emit debug-only message IDs on cache
    # eviction. Audit output has a stricter no-capture-content contract.
    disable_logger()
    reset_before_lines = tuple(args.dedup_reset_before_lines)
    if list(reset_before_lines) != sorted(set(reset_before_lines)):
        print("ERROR kind=invalid_arguments")
        return EXIT_USAGE_OR_IO

    format_file = str(args.format_file) if args.format_file is not None else None
    try:
        formatter = ItemFormatter(format_file)
    except Exception:  # noqa: BLE001 - configuration errors may contain templates
        print("ERROR kind=invalid_format_configuration")
        return EXIT_USAGE_OR_IO

    if args.format_name not in formatter.format_file:
        print("ERROR kind=unknown_format")
        return EXIT_USAGE_OR_IO

    try:
        stats = audit_capture(
            args.jsonl_path,
            args.txt_path,
            formatter=formatter,
            format_name=args.format_name,
            max_seen_message_ids=args.max_seen_message_ids,
            dedup_reset_before_lines=reset_before_lines,
        )
    except InputIdentityError:
        print("ERROR kind=input_identity")
        return EXIT_USAGE_OR_IO
    except OSError:
        print("ERROR kind=input_io")
        return EXIT_USAGE_OR_IO

    print(_format_summary(stats))
    return EXIT_AUDIT_FAILED if stats.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

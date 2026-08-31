# SPDX-License-Identifier: MIT

"""Streaming JSONL/TXT capture artifact parity audit."""

from __future__ import annotations

import io
import json
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, cast

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.sites._message_dedup import _FormattedMessageDeduplicator

if TYPE_CHECKING:
    from pathlib import Path

    from chat_downloader.formatting import ItemFormatter
    from chat_downloader.utils.json_types import JSONDict

_NEWLINE_LF = "lf"
_NEWLINE_CRLF = "crlf"
_NONBLOCKING_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
)
_FileIdentity = tuple[int, int]


class InputIdentityError(OSError):
    """Raised when both artifact paths open the same regular file."""


@dataclass(slots=True)
class AuditStats:
    """Bounded audit state; no captured message content is retained."""

    jsonl_lines: int = 0
    jsonl_records: int = 0
    expected_txt_lines: int = 0
    txt_lines: int = 0
    suppressed_duplicates: int = 0
    jsonl_errors: int = 0
    dedup_errors: int = 0
    render_errors: int = 0
    txt_utf8_errors: int = 0
    text_mismatches: int = 0
    newline_errors: int = 0
    dedup_reset_errors: int = 0
    dedup_resets_applied: int = 0
    count_mismatch: bool = False
    jsonl_trailing_newline: bool | None = None
    txt_trailing_newline: bool | None = None
    jsonl_newline_style: str | None = None
    txt_newline_style: str | None = None
    jsonl_mixed_newlines: int = 0
    txt_mixed_newlines: int = 0
    newline_style_mismatch: bool = False
    jsonl_missing: bool = False
    txt_missing: bool = False
    comparison_complete: bool = True
    comparison_skipped: int = 0
    first_issue: str | None = None
    first_issue_jsonl_line: int | None = None
    first_issue_txt_line: int | None = None
    first_mismatch_jsonl_line: int | None = None
    first_mismatch_txt_line: int | None = None

    @property
    def failed(self) -> bool:
        """Return whether any artifact or parity violation was observed."""
        return (
            self.jsonl_errors > 0
            or self.dedup_errors > 0
            or self.render_errors > 0
            or self.txt_utf8_errors > 0
            or self.text_mismatches > 0
            or self.newline_errors > 0
            or self.dedup_reset_errors > 0
            or self.count_mismatch
            or self.jsonl_trailing_newline is False
            or self.txt_trailing_newline is False
        )


def _record_issue(
    stats: AuditStats,
    reason: str,
    *,
    jsonl_line: int | None = None,
    txt_line: int | None = None,
) -> None:
    """Retain only the first issue's kind and indices."""
    if stats.first_issue is not None:
        return
    stats.first_issue = reason
    stats.first_issue_jsonl_line = jsonl_line
    stats.first_issue_txt_line = txt_line


def _record_mismatch(
    stats: AuditStats,
    reason: str,
    *,
    jsonl_line: int | None = None,
    txt_line: int | None = None,
) -> None:
    """Count a mismatch and retain its first JSONL/TXT positions."""
    stats.text_mismatches += 1
    if (
        stats.first_mismatch_jsonl_line is None
        and stats.first_mismatch_txt_line is None
    ):
        stats.first_mismatch_jsonl_line = jsonl_line
        stats.first_mismatch_txt_line = txt_line
    _record_issue(
        stats,
        reason,
        jsonl_line=jsonl_line,
        txt_line=txt_line,
    )


def _record_newline_error(
    stats: AuditStats,
    reason: str,
    *,
    jsonl_line: int | None = None,
    txt_line: int | None = None,
) -> None:
    """Count a physical-newline error and retain its first positions."""
    stats.newline_errors += 1
    if (
        stats.first_mismatch_jsonl_line is None
        and stats.first_mismatch_txt_line is None
    ):
        stats.first_mismatch_jsonl_line = jsonl_line
        stats.first_mismatch_txt_line = txt_line
    _record_issue(
        stats,
        reason,
        jsonl_line=jsonl_line,
        txt_line=txt_line,
    )


def _open_optional_input(
    stack: ExitStack,
    path: Path,
) -> tuple[IO[bytes], bool, _FileIdentity | None]:
    """Open one regular input, treating only a truly absent path as empty."""
    try:
        descriptor = os.open(path, _NONBLOCKING_READ_FLAGS)
    except FileNotFoundError:
        try:
            path.lstat()
        except FileNotFoundError:
            return stack.enter_context(io.BytesIO()), True, None
        # A directory entry still exists, so open() failed through a dangling
        # symlink or a race. It is not a lazily uncreated output artifact.
        msg = "capture input exists but cannot be opened"
        raise OSError(msg) from None

    # Register descriptor ownership immediately. The wrapped binary stream
    # deliberately leaves it open so ExitStack closes the wrapper first and
    # the descriptor second on success or any later exception.
    stack.callback(os.close, descriptor)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        msg = "capture input is not a regular file"
        raise OSError(msg)
    opened = os.fdopen(descriptor, "rb", closefd=False)
    stream = stack.enter_context(opened)
    return stream, False, (metadata.st_dev, metadata.st_ino)


def _split_physical_line(raw_line: bytes) -> tuple[bytes, str | None]:
    """Separate a physical LF/CRLF terminator from one line's content."""
    if raw_line.endswith(b"\r\n"):
        return raw_line[:-2], _NEWLINE_CRLF
    if raw_line.endswith(b"\n"):
        return raw_line[:-1], _NEWLINE_LF
    return raw_line, None


def _observe_newline(
    stats: AuditStats,
    style: str | None,
    *,
    artifact: str,
    line_number: int,
) -> None:
    """Track final and consistent physical newline style for one artifact."""
    trailing_attribute = f"{artifact}_trailing_newline"
    style_attribute = f"{artifact}_newline_style"
    mixed_attribute = f"{artifact}_mixed_newlines"
    setattr(stats, trailing_attribute, style is not None)
    if style is None:
        return

    first_style = cast("str | None", getattr(stats, style_attribute))
    if first_style is None:
        setattr(stats, style_attribute, style)
        return
    if first_style == style:
        return

    setattr(stats, mixed_attribute, getattr(stats, mixed_attribute) + 1)
    positions = (
        {"jsonl_line": line_number}
        if artifact == "jsonl"
        else {"txt_line": line_number}
    )
    _record_newline_error(stats, f"{artifact}_mixed_newlines", **positions)


def _read_txt_line(
    stream: IO[bytes],
    stats: AuditStats,
) -> tuple[bool, bytes]:
    """Read, split, and UTF-8 validate one physical TXT line."""
    raw_line = stream.readline()
    if not raw_line:
        return False, b""

    stats.txt_lines += 1
    content, newline_style = _split_physical_line(raw_line)
    _observe_newline(
        stats,
        newline_style,
        artifact="txt",
        line_number=stats.txt_lines,
    )
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        stats.txt_utf8_errors += 1
        _record_issue(stats, "txt_invalid_utf8", txt_line=stats.txt_lines)
    return True, content


def _parse_jsonl_line(
    content: bytes,
    line_number: int,
    stats: AuditStats,
) -> JSONDict | None:
    """Decode one JSONL line and require a JSON object."""
    try:
        decoded_line = content.decode("utf-8")
    except UnicodeDecodeError:
        stats.jsonl_errors += 1
        _record_issue(stats, "jsonl_invalid_utf8", jsonl_line=line_number)
        return None

    if not decoded_line.strip():
        stats.jsonl_errors += 1
        _record_issue(stats, "jsonl_blank_line", jsonl_line=line_number)
        return None

    try:
        value = json.loads(decoded_line)
    except (ValueError, RecursionError):
        stats.jsonl_errors += 1
        _record_issue(stats, "jsonl_invalid_json", jsonl_line=line_number)
        return None

    if not isinstance(value, dict):
        stats.jsonl_errors += 1
        _record_issue(stats, "jsonl_non_object", jsonl_line=line_number)
        return None

    stats.jsonl_records += 1
    return cast("JSONDict", value)


def _render_expected_line(
    formatter: ItemFormatter,
    format_name: str,
    item: JSONDict,
) -> bytes:
    """Render production TXT content without assuming the host newline."""
    rendered = formatter.format(item, format_name=format_name)
    return rendered.encode("utf-8")


def _mark_comparison_incomplete(
    stats: AuditStats,
    *,
    current_line_already_skipped: bool,
) -> None:
    """Mark alignment loss and count the current un-compared JSONL line."""
    if not current_line_already_skipped:
        stats.comparison_skipped += 1
    stats.comparison_complete = False


def audit_capture(  # noqa: C901 - one streaming state machine validates both files
    jsonl_path: Path,
    txt_path: Path,
    *,
    formatter: ItemFormatter,
    format_name: str,
    max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
    dedup_reset_before_lines: tuple[int, ...] = (),
) -> AuditStats:
    """Stream both artifacts and return content-free parity statistics."""
    stats = AuditStats()
    deduplicator = _FormattedMessageDeduplicator(max_seen_message_ids)
    next_reset_index = 0

    with ExitStack() as stack:
        (
            jsonl_stream,
            stats.jsonl_missing,
            jsonl_identity,
        ) = _open_optional_input(stack, jsonl_path)
        txt_stream, stats.txt_missing, txt_identity = _open_optional_input(
            stack,
            txt_path,
        )
        if jsonl_identity is not None and jsonl_identity == txt_identity:
            msg = "JSONL and TXT inputs resolve to the same file"
            raise InputIdentityError(msg)

        for jsonl_line, raw_line in enumerate(jsonl_stream, start=1):
            stats.jsonl_lines += 1
            if (
                next_reset_index < len(dedup_reset_before_lines)
                and dedup_reset_before_lines[next_reset_index] == jsonl_line
            ):
                deduplicator = _FormattedMessageDeduplicator(max_seen_message_ids)
                stats.dedup_resets_applied += 1
                next_reset_index += 1

            line_already_skipped = not stats.comparison_complete
            if line_already_skipped:
                stats.comparison_skipped += 1

            content, newline_style = _split_physical_line(raw_line)
            _observe_newline(
                stats,
                newline_style,
                artifact="jsonl",
                line_number=jsonl_line,
            )
            item = _parse_jsonl_line(content, jsonl_line, stats)
            if item is None:
                _mark_comparison_incomplete(
                    stats,
                    current_line_already_skipped=line_already_skipped,
                )
                continue

            try:
                should_emit = deduplicator.should_emit(item)
            except Exception:  # noqa: BLE001 - item errors must remain content-free
                stats.dedup_errors += 1
                _record_issue(stats, "dedup_error", jsonl_line=jsonl_line)
                _mark_comparison_incomplete(
                    stats,
                    current_line_already_skipped=line_already_skipped,
                )
                continue

            if not should_emit:
                stats.suppressed_duplicates += 1
                continue

            try:
                expected_line = _render_expected_line(formatter, format_name, item)
            except Exception:  # noqa: BLE001 - privacy boundary must not echo item data
                stats.render_errors += 1
                _record_issue(stats, "render_error", jsonl_line=jsonl_line)
                _mark_comparison_incomplete(
                    stats,
                    current_line_already_skipped=line_already_skipped,
                )
                continue

            stats.expected_txt_lines += 1
            if not stats.comparison_complete:
                continue

            expected_txt_line = stats.expected_txt_lines
            actual_present, actual_content = _read_txt_line(txt_stream, stats)
            if not actual_present or actual_content != expected_line:
                _record_mismatch(
                    stats,
                    "text_mismatch",
                    jsonl_line=jsonl_line,
                    txt_line=expected_txt_line,
                )

        first_extra_txt_line: int | None = None
        while True:
            txt_line_present, _ = _read_txt_line(txt_stream, stats)
            if not txt_line_present:
                break
            if first_extra_txt_line is None:
                first_extra_txt_line = stats.txt_lines

        if next_reset_index < len(dedup_reset_before_lines):
            stats.dedup_reset_errors = len(dedup_reset_before_lines) - next_reset_index
            stats.comparison_complete = False
            _record_issue(
                stats,
                "dedup_reset_out_of_range",
                jsonl_line=dedup_reset_before_lines[next_reset_index],
            )

        stats.count_mismatch = stats.expected_txt_lines != stats.txt_lines
        if stats.count_mismatch:
            if stats.comparison_complete and first_extra_txt_line is not None:
                _record_mismatch(
                    stats,
                    "text_count_mismatch",
                    txt_line=first_extra_txt_line,
                )
            else:
                _record_issue(stats, "text_count_mismatch")

        if stats.jsonl_trailing_newline is False:
            _record_newline_error(
                stats,
                "jsonl_missing_trailing_newline",
                jsonl_line=stats.jsonl_lines,
            )

        if stats.txt_trailing_newline is False:
            _record_newline_error(
                stats,
                "txt_missing_trailing_newline",
                txt_line=stats.txt_lines,
            )

        if (
            stats.jsonl_newline_style is not None
            and stats.txt_newline_style is not None
            and stats.jsonl_newline_style != stats.txt_newline_style
        ):
            stats.newline_style_mismatch = True
            _record_newline_error(
                stats,
                "capture_newline_style_mismatch",
                jsonl_line=1,
                txt_line=1,
            )

    return stats

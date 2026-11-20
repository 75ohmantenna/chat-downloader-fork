# SPDX-License-Identifier: MIT

"""Bounded inspection and repair of append boundaries on pinned output files."""

from __future__ import annotations

import json
import os
from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from chat_downloader.debugging import log
from chat_downloader.utils._bounded_records import (
    MAX_CAPTURE_RECORD_BYTES,
    CaptureRecordLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO

_TAIL_SCAN_BYTES = 8192
_JSONL_BOMS = (BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE)


def _append_signature(file: BinaryIO) -> tuple[int, int]:
    """Identify changes to the pinned file without reopening its path."""
    info = os.fstat(file.fileno())
    return info.st_size, info.st_mtime_ns


def _newline_at(file: BinaryIO, end: int) -> str:
    """Identify the terminator ending at this byte offset."""
    file.seek(max(0, end - 2))
    return "\r\n" if file.read(min(2, end)) == b"\r\n" else "\n"


@dataclass(frozen=True)
class _FileActivation:
    """Plan output initialization without changing any bytes."""

    newline: str = field(default_factory=lambda: os.linesep)
    truncate_at: int | None = None
    terminator: bytes = b""
    repair_action: str = ""
    append_signature: tuple[int, int] | None = None

    @classmethod
    def inspect_append(
        cls,
        file: BinaryIO,
        file_name: str,
        inspect_tail: Callable[[BinaryIO, str], _FileActivation],
    ) -> _FileActivation:
        """Record file metadata before inspecting its append boundary."""
        signature = _append_signature(file)
        return replace(inspect_tail(file, file_name), append_signature=signature)

    def apply(self, file: BinaryIO, file_name: str) -> None:
        """Apply the planned changes on the inspected stream."""
        if (
            self.append_signature is not None
            and self.append_signature != _append_signature(file)
        ):
            msg = f"Output file changed during append setup: {file_name}"
            raise OSError(msg)
        if self.truncate_at is not None:
            file.truncate(self.truncate_at)
        file.seek(0, os.SEEK_END)
        if self.terminator:
            file.write(self.terminator)
        if self.repair_action:
            file.flush()
            os.fsync(file.fileno())
            log("warning", f"{self.repair_action} {file_name} before append.")


def _terminate_final_line(
    end: int, last_byte: bytes, newline: str, action: str
) -> _FileActivation:
    """Plan to replace a partial CR terminator rather than duplicate it."""
    return _FileActivation(
        newline,
        truncate_at=end - 1 if last_byte == b"\r" else None,
        terminator=newline.encode("ascii"),
        repair_action=action,
    )


def _inspect_text_final_line(file: BinaryIO, _file_name: str) -> _FileActivation:
    """Terminate the final line and retain the last existing newline style."""
    end = file.seek(0, os.SEEK_END)
    if not end:
        return _FileActivation()
    file.seek(end - 1)
    last_byte = file.read(1)
    if last_byte == b"\n":
        return _FileActivation(_newline_at(file, end))
    newline: str = "\r\n" if last_byte == b"\r" else os.linesep
    scan_start = max(0, end - MAX_CAPTURE_RECORD_BYTES)
    position = end
    while position > scan_start:
        start = max(scan_start, position - _TAIL_SCAN_BYTES)
        file.seek(start)
        index = file.read(position - start).rfind(b"\n")
        if index >= 0:
            newline = _newline_at(file, start + index + 1)
            break
        position = start
    return _terminate_final_line(
        end, last_byte, newline, "Terminated the final text line in"
    )


def _check_jsonl_append_encoding(file: BinaryIO, end: int, file_name: str) -> bytes:
    """Inspect bounded encoding samples and return the last byte."""
    file.seek(0)
    prefix = file.read(min(4, end))
    # JSON syntax in UTF-16/32 includes raw NULs, including in the encoded LF.
    # Keep the bytes on both sides of an apparent LF so it cannot mask a NUL.
    file.seek(max(0, end - _TAIL_SCAN_BYTES))
    suffix = file.read(min(end, _TAIL_SCAN_BYTES))
    if (
        b"\0" in prefix
        or b"\0" in suffix
        or prefix.startswith(_JSONL_BOMS)
        or any(b"\n" + bom in suffix for bom in _JSONL_BOMS)
    ):
        msg = f"JSONL file {file_name} must be UTF-8 without a BOM."
        raise ValueError(msg)
    return suffix[-1:]


def _inspect_jsonl_final_line(file: BinaryIO, file_name: str) -> _FileActivation:
    """Plan repair after inspecting at most one record and its boundary."""
    end = file.seek(0, os.SEEK_END)
    if not end:
        return _FileActivation()
    last_byte = _check_jsonl_append_encoding(file, end, file_name)
    if last_byte == b"\n":
        return _FileActivation(_newline_at(file, end))

    # Allow a partial CR terminator outside the record's content-byte limit.
    tail_limit = MAX_CAPTURE_RECORD_BYTES + (last_byte == b"\r")
    scan_start = max(0, end - tail_limit - 1)
    position = end
    truncate_at = 0
    while position > scan_start:
        chunk_start = max(scan_start, position - _TAIL_SCAN_BYTES)
        file.seek(chunk_start)
        chunk = file.read(position - chunk_start)
        newline_index = chunk.rfind(b"\n")
        if newline_index >= 0:
            truncate_at = chunk_start + newline_index + 1
            break
        position = chunk_start

    if end - truncate_at > tail_limit:
        raise CaptureRecordLimitError
    newline = (
        _newline_at(file, truncate_at)
        if truncate_at
        else "\r\n"
        if last_byte == b"\r"
        else os.linesep
    )

    file.seek(truncate_at)
    tail = file.read(end - truncate_at)
    if b"\0" in tail or tail.startswith(_JSONL_BOMS):
        msg = f"JSONL file {file_name} must be UTF-8 without a BOM."
        raise ValueError(msg)
    try:
        json.loads(tail.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _FileActivation(
            newline,
            truncate_at=truncate_at,
            repair_action="Removed an incomplete trailing JSONL record from",
        )
    return _terminate_final_line(
        end, last_byte, newline, "Terminated the final JSONL record in"
    )

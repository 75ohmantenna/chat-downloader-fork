# SPDX-License-Identifier: MIT

"""Provider-neutral byte limits for physical capture records."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import IO

# Match the largest supported Kick archival record. Terminators do not count.
MAX_CAPTURE_RECORD_BYTES = 16 * 1024 * 1024


class CaptureRecordLimitError(OSError):
    """Reject oversized input without retaining or disclosing record content."""

    def __init__(self) -> None:
        super().__init__(
            f"Capture record exceeds the {MAX_CAPTURE_RECORD_BYTES}-byte limit."
        )


def read_bounded_record(stream: IO[bytes]) -> bytes:
    """Read at most one record plus two LF/CRLF sentinel bytes before decoding.

    The limit excludes a final LF or CRLF. Oversized input is a terminal error:
    callers must not treat the bounded prefix as a complete or malformed record.
    """
    line = stream.readline(MAX_CAPTURE_RECORD_BYTES + 2)
    size = len(line)
    if line.endswith(b"\r\n"):
        size -= 2
    elif line.endswith(b"\n"):
        size -= 1
    if size > MAX_CAPTURE_RECORD_BYTES:
        raise CaptureRecordLimitError
    return line


def iter_bounded_records(stream: IO[bytes]) -> Iterator[bytes]:
    """Yield complete physical records, failing before oversized decode/parse."""
    while line := read_bounded_record(stream):
        yield line

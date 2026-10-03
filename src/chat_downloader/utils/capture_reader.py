# SPDX-License-Identifier: MIT

"""Strict, provider-neutral JSONL capture reading with physical line findings."""

from __future__ import annotations

import json
import math
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path
    from typing import BinaryIO

    from chat_downloader.utils.json_types import JSONDict


@dataclass(frozen=True, slots=True)
class CaptureLine:
    """One physical line, containing either an object or a content-free finding."""

    line_number: int
    record: JSONDict | None
    issue: Literal["invalid_jsonl", "non_object_record"] | None


@contextmanager
def open_capture_input(path: Path) -> Iterator[BinaryIO]:
    """Open a regular file without waiting for a FIFO writer; always close it."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(descriptor)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _validate_json(value: object) -> None:
    """Reject non-finite numbers and lone surrogates, even in unused fields."""
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json(key)
            _validate_json(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item)


def read_capture_lines(path: Path) -> Generator[CaptureLine, None, None]:
    """Read strict UTF-8 objects, continuing after invalid physical lines.

    Findings contain no input content. File errors propagate to the caller.
    Consumers that stop early must close the iterator to release its input.
    """
    with open_capture_input(path) as source:
        for line_number, line in enumerate(source, 1):
            try:
                record: object = json.loads(
                    line.decode("utf-8"), object_pairs_hook=_json_object
                )
                _validate_json(record)
            except (UnicodeError, ValueError, RecursionError):
                yield CaptureLine(line_number, None, "invalid_jsonl")
                continue
            if not isinstance(record, dict):
                yield CaptureLine(line_number, None, "non_object_record")
                continue
            yield CaptureLine(line_number, cast("JSONDict", record), None)

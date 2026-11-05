# SPDX-License-Identifier: MIT

"""Provider-neutral capture input, strict JSONL findings, and literal run summaries."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from chat_downloader.utils._bounded_records import iter_bounded_records

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path
    from typing import BinaryIO

    from chat_downloader.utils.json_types import JSONDict

_SUMMARY_PREFIX = re.compile(
    rb"(?:\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] )?"
    rb"\[DEBUG\] Run summary: "
)
_SUMMARY_LIMIT = 65536
_INVALID_SUMMARY = "invalid_run_summary"


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


def _decode_summary(line: bytes) -> dict[str, object]:
    """Reject ambiguous dictionary keys before evaluating a literal summary."""
    tree = ast.parse(line.decode("utf-8"), mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [ast.literal_eval(key) for key in node.keys if key is not None]
            if len(keys) != len(set(keys)):
                raise ValueError(_INVALID_SUMMARY)
    summary: object = ast.literal_eval(tree)
    if not isinstance(summary, dict):
        raise TypeError(_INVALID_SUMMARY)
    return summary


def read_debug_summary(path: Path) -> dict[str, object]:
    """Read exactly one bounded Python-literal summary, separate from JSONL policy."""
    result: dict[str, object] | None = None
    with open_capture_input(path) as source:
        while line := source.readline(_SUMMARY_LIMIT + 1):
            prefix = _SUMMARY_PREFIX.match(line)
            if prefix is not None:
                if result is not None or len(line) > _SUMMARY_LIMIT:
                    raise ValueError(_INVALID_SUMMARY)
                try:
                    result = _decode_summary(line[prefix.end() :])
                except OverflowError as error:
                    msg = _INVALID_SUMMARY
                    raise ValueError(msg) from error
            # Drain unrelated physical lines before recognizing another header.
            while not line.endswith(b"\n") and len(line) == _SUMMARY_LIMIT + 1:
                line = source.readline(_SUMMARY_LIMIT + 1)
    if result is None:
        raise ValueError(_INVALID_SUMMARY)
    return result


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

    Findings contain no input content. File and physical-record-limit errors
    propagate to the caller before oversized UTF-8 decode or JSON parsing.
    Consumers that stop early must close the iterator to release its input.
    """
    with open_capture_input(path) as source:
        for line_number, line in enumerate(iter_bounded_records(source), 1):
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

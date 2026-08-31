# SPDX-License-Identifier: MIT

"""JSONL datetime serialization errors remain explicit."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING

import pytest

from chat_downloader.output.continuous_write import JsonLinesContinuousWriter

if TYPE_CHECKING:
    from pathlib import Path


def test_jsonl_rejects_naive_datetime(tmp_path: Path) -> None:
    writer = JsonLinesContinuousWriter(str(tmp_path / "out.jsonl"))
    try:
        with pytest.raises(ValueError, match="naive datetime"):
            writer.write({"timestamp": datetime.datetime(2024, 1, 1, 12, 0, 0)})  # noqa: DTZ001 — intentional naive datetime to test rejection
    finally:
        writer.close()


def test_jsonl_writes_pre_serialized_utc_datetime(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        aware = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        writer.write({"ts_iso": aware.isoformat(), "ok": True})
    finally:
        writer.close()
    with open(path, encoding="utf-8") as f:
        assert json.loads(f.read()) == {
            "ok": True,
            "ts_iso": "2024-01-01T12:00:00+00:00",
        }


def test_jsonl_rejects_raw_utc_aware_datetime(tmp_path: Path) -> None:
    writer = JsonLinesContinuousWriter(str(tmp_path / "out.jsonl"))
    try:
        aware = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        with pytest.raises(TypeError, match="not JSON serializable"):
            writer.write({"timestamp": aware})
    finally:
        writer.close()


def test_jsonl_assert_passes_non_dict_items(tmp_path: Path) -> None:
    """Non-dict items skip the dict-only datetime scan and serialize."""
    path = tmp_path / "out.jsonl"
    writer = JsonLinesContinuousWriter(str(path))
    try:
        writer.write([1, 2, 3])
    finally:
        writer.close()
    with open(path, encoding="utf-8") as f:
        assert [json.loads(line) for line in f] == [[1, 2, 3]]


def test_jsonl_assert_only_inspects_top_level(tmp_path: Path) -> None:
    """The JSON encoder rejects nested datetimes skipped by the top-level guard."""
    writer = JsonLinesContinuousWriter(str(tmp_path / "out.jsonl"))
    try:
        nested = {"nested": {"ts": datetime.datetime(2024, 1, 1)}}  # noqa: DTZ001 — intentional nested naive datetime
        with pytest.raises(TypeError, match="not JSON serializable"):
            writer.write(nested)
    finally:
        writer.close()

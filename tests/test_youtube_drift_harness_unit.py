# SPDX-License-Identifier: MIT

"""Drift regression harness for YouTube chat parsing.

Each fixture in ``tests/fixtures/youtube/live_events/`` that contains a raw
continuation response (dict, not a pre-parsed event list) is replayed through
the full message pipeline.  The test asserts that the spy-patched ``debug_log``
is **never** called with a drift-sentinel message, so previously-fixed drift
becomes a permanent regression anchor.

Workflow for new drift
----------------------
1. Run with ``CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`` and optionally set
   ``CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR``; otherwise capture uses a temp directory.
2. Identify the cause (unknown action, unknown message type, missing key).
3. Fix the parsing code.
4. Review the capture, copy the raw continuation to
   ``tests/fixtures/youtube/live_events/``, and rename it to ``*.json``.
5. Run this harness — it must pass before merging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import chat_downloader.debugging as _dbg
import chat_downloader.sites.youtube.parsing.actions_handlers_validation as _ahv
import chat_downloader.sites.youtube.parsing.actions_router as _ar
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuations import (
    parse_continuation_response,
)
from chat_downloader.sites.youtube.message_pipeline import (
    process_pipeline_action,
)

_FX_DIR = Path(__file__).resolve().parent / "fixtures" / "youtube" / "live_events"

# Sentinel substrings that indicate a parsing gap.
_DRIFT_MARKERS = (
    "Unknown action",
    "Unknown message type",
    "Missing keys found",
    "Parse of action returned empty",
)


def _is_raw_continuation(payload: Any) -> bool:
    """Return True if the fixture is a raw continuation response dict."""
    return isinstance(payload, dict)


def _raw_continuation_fixtures() -> list[Path]:
    """Return all live_events fixtures that are raw continuation dicts."""
    paths = []
    for path in sorted(_FX_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover
            continue
        if _is_raw_continuation(payload):
            paths.append(path)
    return paths


@pytest.fixture
def drift_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Spy on debug_log in all parsing modules; record drift-sentinel calls."""
    seen: list[str] = []

    def _spy(*args: Any, **_kwargs: Any) -> None:
        text = " ".join(str(a) for a in args)
        if any(marker in text for marker in _DRIFT_MARKERS):
            seen.append(text)

    # Patch the name in the source module and in every module that has already
    # imported it (because they bound the name at import time).
    monkeypatch.setattr(_dbg, "debug_log", _spy)
    monkeypatch.setattr(_ar, "debug_log", _spy)
    monkeypatch.setattr(_ahv, "debug_log", _spy)
    return seen


@pytest.mark.parametrize(
    "path",
    _raw_continuation_fixtures(),
    ids=lambda p: p.name,
)
def test_continuation_fixture_parses_without_drift(
    path: Path, drift_recorder: list[str]
) -> None:
    """All actions in the fixture must parse without triggering drift logs."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = parse_continuation_response(payload)
    msg_filter = MessageFilter(
        _MESSAGE_GROUPS, groups_to_add=["all"], types_to_add=None
    )

    for action in result.actions:
        process_pipeline_action(json.loads(json.dumps(action)), 0, msg_filter, None)

    assert not drift_recorder, f"{path.name} triggered drift log(s):\n" + "\n".join(
        f"  {line}" for line in drift_recorder
    )

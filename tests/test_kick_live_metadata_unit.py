# SPDX-License-Identifier: MIT

"""Live stream metadata reaches the public Chat object and run manifest."""

from __future__ import annotations

import json

import pytest

from chat_downloader.runtime.capture_manifest import RunManifest
from chat_downloader.runtime.runner import RunResult
from tests.kick_helpers import (
    FakeDownloader,
    FakeKickSession,
    FakeResponse,
    build_live_chat,
    load_fixture,
    session_patch,
)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-10-13 19:31:13", 1_791_919_873_000_000),
        ("2026-10-13T21:31:13+02:00", 1_791_919_873_000_000),
        ("2026-10-13T19:31:13.123456Z", 1_791_919_873_123_456),
        ("9999-12-31T23:59:59.999999Z", 253_402_300_799_999_999),
        ("2026", None),
        ("2026-10", None),
        ("2026-10-13", None),
        ("2026-10-13+19:31:13", None),
        ("0001-01-01T00:00:00+01:00", None),
        ("9999-12-31T23:59:59-01:00", None),
        (None, None),
        ("", None),
        ("invalid", None),
        (True, None),
    ],
)
def test_live_stream_start_time_reaches_manifest(tmp_path, timestamp, expected):
    data = load_fixture("channel_live.json")
    data["livestream"]["start_time"] = timestamp
    with session_patch(FakeKickSession([FakeResponse(payload=data)])):
        chat = build_live_chat(FakeDownloader())
    assert chat.start_time == expected
    path = tmp_path / "run.json"
    RunManifest(str(path), None).write(chat, RunResult(success=True))
    assert json.loads(path.read_text())["recording"]["start_time"] == expected


def test_offline_channel_has_no_stream_start_time():
    data = load_fixture("channel_live.json")
    data["livestream"] = None
    with session_patch(FakeKickSession([FakeResponse(payload=data)])):
        chat = build_live_chat(FakeDownloader())
    assert chat.start_time is None

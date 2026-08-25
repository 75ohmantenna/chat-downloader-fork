# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    MESSAGE_DELETED_EVENT,
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_ERROR,
    PUSHER_PING,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.errors import KickError
from chat_downloader.sites.kick.parsing import events
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture, pusher_frame

if TYPE_CHECKING:
    from pathlib import Path


def test_chat_message_event_is_parsed() -> None:
    data = load_fixture("chat_message_event_data.json")
    message = dispatch_event(pusher_frame(CHAT_MESSAGE_EVENT, data))
    assert message is not None
    assert message["message_id"] == "live-1"
    assert message["message"] == "hello world :PogU:"


def test_chat_message_event_with_object_data() -> None:
    # Some frames may provide an already-decoded object instead of a string.
    data = load_fixture("chat_message_event_data.json")
    message = dispatch_event({"event": CHAT_MESSAGE_EVENT, "data": data})
    assert message is not None
    assert message["message_id"] == "live-1"


def test_unknown_chat_message_type_records_diagnostic() -> None:
    diagnostics: list[str] = []
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT,
        {"id": "future", "type": "future_type", "content": "message"},
    )

    message = dispatch_event(frame, record_diagnostic=diagnostics.append)

    assert message is not None
    assert message["message_type"] == "text_message"
    assert diagnostics == ["unknown_message_type_count", "parsed_event_count"]


def test_reply_context_survives_event_dispatch() -> None:
    data = load_fixture("reply_message_event_data.json")

    message = dispatch_event(pusher_frame(CHAT_MESSAGE_EVENT, data))

    assert message is not None
    assert message["in_reply_to"]["message_id"] == "original-message"
    assert message["in_reply_to"]["author"]["display_name"] == "OriginalAuthor"


def test_ai_moderation_context_survives_event_dispatch() -> None:
    data = load_fixture("message_deleted_event_ai.json")

    message = dispatch_event(pusher_frame(MESSAGE_DELETED_EVENT, data))

    assert message is not None
    assert message["metadata"]["ai_moderated"] is True
    assert message["metadata"]["violated_rules"] == ["hate", "harassment"]


def test_malformed_nested_json_is_skipped() -> None:
    frame = {"event": CHAT_MESSAGE_EVENT, "data": "{not valid json"}
    assert dispatch_event(frame) is None


def test_unparseable_chat_payload_is_skipped() -> None:
    # Valid JSON, but missing the required id.
    assert dispatch_event(pusher_frame(CHAT_MESSAGE_EVENT, {"content": "x"})) is None


def test_pusher_error_is_captured_before_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []
    monkeypatch.setattr(
        events,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    with pytest.raises(KickError):
        dispatch_event(pusher_frame(PUSHER_ERROR, {"message": "bad"}))

    assert captured[0][0][0] == "kick-pusher-error"
    assert captured[0][1]["sample_limit"] == 10


@pytest.mark.parametrize(
    "event",
    [PUSHER_CONNECTION_ESTABLISHED, PUSHER_SUBSCRIPTION_SUCCEEDED, PUSHER_PING],
)
def test_control_events_are_ignored(event: str) -> None:
    assert dispatch_event(pusher_frame(event, {})) is None


def test_unknown_event_is_captured_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []
    monkeypatch.setattr(
        events,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    frame = pusher_frame("App\\Events\\FutureEvent", {"future": True})

    assert dispatch_event(frame) is None

    assert captured[0][0] == (
        "kick-unknown-event",
        {"raw": frame, "event_name": "App\\Events\\FutureEvent"},
    )
    assert captured[0][1]["sample_limit"] == 10


def test_malformed_known_event_is_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []
    monkeypatch.setattr(
        events,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    frame = pusher_frame("App\\Events\\SubscriptionEvent", {})

    assert dispatch_event(frame) is None

    assert captured[0][0][0] == "kick-malformed-event"
    assert captured[0][0][1]["raw"] == frame
    assert captured[0][0][1]["message_type"] == "subscription"


def test_frame_without_event_is_captured_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []
    monkeypatch.setattr(
        events,
        "capture_debug_sample",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    assert dispatch_event({"data": "{}"}) is None

    assert captured[0][0][0] == "kick-unknown-event"
    assert captured[0][0][1]["reason"] == "missing or non-string event name"


def test_unknown_event_capture_is_sanitized_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    caplog.set_level("DEBUG", logger=events.logger.name)
    frame = {
        "event": "App\\Events\\FutureEvent",
        "data": {
            "authorization": "Bearer should-not-survive",
            "message": "visible\u001btext",
            "url": "https://example.test/watch?token=should-not-survive&safe=yes",
        },
    }

    assert dispatch_event(frame) is None

    samples = list(sample_dir.glob("kick-unknown-event-*.json"))
    assert len(samples) == 1
    sample_text = samples[0].read_text(encoding="utf-8")
    captured = json.loads(sample_text)
    assert captured["raw"]["data"]["authorization"] == "<redacted>"
    assert captured["raw"]["data"]["message"] == "visible\u001btext"
    assert captured["raw"]["data"]["url"].startswith(
        "https://example.test/watch?token=<redacted>"
    )
    assert "should-not-survive" not in sample_text
    assert "\u001b" not in sample_text
    assert "\\u001b" in sample_text
    assert stat.S_IMODE(samples[0].stat().st_mode) == 0o600


def test_unknown_event_capture_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    caplog.set_level("DEBUG", logger=events.logger.name)

    for index in range(12):
        dispatch_event(pusher_frame(f"App\\Events\\Future{index}", {"index": index}))

    assert len(list(sample_dir.glob("kick-unknown-event-*.json"))) == 10
    assert "Debug sample limit reached" in caplog.text

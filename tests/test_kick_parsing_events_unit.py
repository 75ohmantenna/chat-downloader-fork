# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import stat

import pytest

from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    MESSAGE_DELETED_EVENT,
    PINNED_MESSAGE_DELETED_EVENT,
    POLL_DELETE_EVENT,
    POLL_UPDATE_EVENT,
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_ERROR,
    PUSHER_PING,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
    STREAM_HOST_EVENT,
    SUBSCRIPTION_EVENT,
)
from chat_downloader.sites.kick.errors import KickError
from chat_downloader.sites.kick.parsing import events
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture, pusher_frame, raw_message


@pytest.fixture
def captured(monkeypatch):
    samples = []
    monkeypatch.setattr(
        events,
        "capture_debug_sample",
        lambda *args, **kwargs: samples.append((args, kwargs)),
    )
    return samples


@pytest.fixture
def sample_dir(tmp_path, monkeypatch, caplog):
    directory = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(directory))
    caplog.set_level("DEBUG", logger=events.logger.name)
    return directory


@pytest.mark.parametrize("encoded", [False, True])
def test_chat_message_event_is_parsed(encoded):
    data = load_fixture("chat_message_event_data.json")
    frame = (
        pusher_frame(CHAT_MESSAGE_EVENT, data)
        if encoded
        else {"event": CHAT_MESSAGE_EVENT, "data": data}
    )
    message = dispatch_event(frame)
    assert message is not None
    assert message["message_id"] == "live-1"
    assert message["message"] == "hello world :PogU:"


def test_unknown_chat_message_type_records_diagnostic():
    diagnostics = []
    frame = pusher_frame(
        CHAT_MESSAGE_EVENT, raw_message("future", type="future_type", content="message")
    )
    message = dispatch_event(frame, record_diagnostic=diagnostics.append)
    assert message is not None
    assert message["message_type"] == "text_message"
    assert diagnostics == ["unknown_message_type_count", "parsed_event_count"]


@pytest.mark.parametrize(
    ("event", "fixture", "expected"),
    [
        (
            CHAT_MESSAGE_EVENT,
            "reply_message_event_data.json",
            {
                ("in_reply_to", "message_id"): "original-message",
                ("in_reply_to", "author", "display_name"): "OriginalAuthor",
            },
        ),
        (
            CHAT_MESSAGE_EVENT,
            "celebration_message_event_data.json",
            {
                ("message_type",): "text_message",
                ("metadata", "celebration", "total_months"): 20,
            },
        ),
        (
            MESSAGE_DELETED_EVENT,
            "message_deleted_event_ai.json",
            {
                ("metadata", "ai_moderated"): True,
                ("metadata", "violated_rules"): ["hate", "harassment"],
            },
        ),
    ],
)
def test_nested_context_survives_event_dispatch(event, fixture, expected):
    diagnostics = []
    message = dispatch_event(
        pusher_frame(event, load_fixture(fixture)), record_diagnostic=diagnostics.append
    )
    assert message is not None
    for path, value in expected.items():
        actual = message
        for key in path:
            actual = actual[key]
        assert actual == value
        if isinstance(value, bool):
            assert actual is value
    assert diagnostics == ["parsed_event_count"]


@pytest.mark.parametrize(
    ("event", "fixture", "received", "expected"),
    [
        (
            SUBSCRIPTION_EVENT,
            "subscription_event_compact.json",
            1_789_000_000_000_001,
            {
                "message_id": "kick-subscription:1789000000000001",
                "message_type": "subscription",
                "message": "",
                "author": {
                    "display_name": "compactsubscriber",
                    "name": "compactsubscriber",
                },
                "metadata": {"months": 1},
            },
        ),
        (
            PINNED_MESSAGE_DELETED_EVENT,
            "pinned_message_deleted_event_empty.json",
            1_789_000_000_000_002,
            {
                "message_id": "kick-unpin:1789000000000002",
                "message_type": "pinned_message_deleted",
                "message": "",
            },
        ),
        (
            STREAM_HOST_EVENT,
            "stream_host_event_compact.json",
            123,
            {
                "message_id": "kick-stream-host:123",
                "message_type": "stream_host",
                "message": "",
                "author": {"display_name": "hosting_user", "name": "hosting_user"},
                "metadata": {"host_username": "hosting_user", "number_viewers": 1044},
            },
        ),
    ],
)
def test_compact_context_and_receive_time_fallback(event, fixture, received, expected):
    data = load_fixture(fixture)
    original = json.loads(json.dumps(data))
    diagnostics = []
    message = dispatch_event(
        pusher_frame(event, data),
        received_timestamp=received,
        record_diagnostic=diagnostics.append,
    )
    assert message == expected
    assert data == original
    assert diagnostics == ["parsed_event_count"]


@pytest.mark.parametrize(
    "provider_id",
    [pytest.param(None, id="null"), "provider-id"],
)
def test_poll_update_uses_receive_time_fallback_id(provider_id):
    diagnostics = []
    payload = load_fixture("poll_update_event.json") | {"id": provider_id}
    message = dispatch_event(
        pusher_frame(POLL_UPDATE_EVENT, payload),
        record_diagnostic=diagnostics.append,
        received_timestamp=1_789_000_000_000_003,
    )
    assert message is not None
    assert message["message_id"] == (provider_id or "kick-poll-update:1789000000000003")
    assert message["message_type"] == "poll_update"
    assert message["message"] == "Example poll"
    assert message["metadata"]["options"][1]["votes"] == 1
    assert diagnostics == ["parsed_event_count"]


@pytest.mark.parametrize("payload", [None, {}, [], {"id": None}])
def test_poll_deleted_ignores_payload_shape(payload):
    diagnostics = []
    message = dispatch_event(
        pusher_frame(POLL_DELETE_EVENT, payload),
        record_diagnostic=diagnostics.append,
        received_timestamp=1_789_000_000_000_004,
    )
    assert message == {
        "message_id": "kick-poll-deleted:1789000000000004",
        "message_type": "poll_deleted",
        "message": "",
    }
    assert diagnostics == ["parsed_event_count"]


@pytest.mark.parametrize("received_timestamp", [None, True, -1])
@pytest.mark.parametrize(
    ("event", "payload"),
    [
        (SUBSCRIPTION_EVENT, load_fixture("subscription_event_compact.json")),
        (
            PINNED_MESSAGE_DELETED_EVENT,
            load_fixture("pinned_message_deleted_event_empty.json"),
        ),
        (POLL_UPDATE_EVENT, {"poll": {"title": "Poll"}}),
        (POLL_DELETE_EVENT, None),
        (STREAM_HOST_EVENT, load_fixture("stream_host_event_compact.json")),
    ],
)
def test_compact_events_require_valid_receive_timestamp(
    event,
    payload,
    received_timestamp,
):
    diagnostics = []
    message = dispatch_event(
        pusher_frame(event, payload),
        received_timestamp=received_timestamp,
        record_diagnostic=diagnostics.append,
    )
    assert message is None
    if event in (POLL_UPDATE_EVENT, POLL_DELETE_EVENT):
        kind = "poll_update" if event == POLL_UPDATE_EVENT else "poll_deleted"
        assert diagnostics == ["malformed_event_count", f"malformed_event_type:{kind}"]


@pytest.mark.parametrize(
    ("event", "payload", "received", "kind"),
    [
        *[
            (SUBSCRIPTION_EVENT, payload, 1_789_000_000_000_003, "subscription")
            for payload in [
                {"chatroom_id": 1, "username": "subscriber", "months": 0},
                {"chatroom_id": 1, "username": "subscriber", "months": True},
                {"chatroom_id": 1, "username": "", "months": 1},
                {"username": "subscriber", "months": 1},
                {"chatroom_id": 1, "username": "subscriber", "months": 1, "sender": {}},
            ]
        ],
        (
            PINNED_MESSAGE_DELETED_EVENT,
            [{"id": "unexpected"}],
            1_789_000_000_000_004,
            "pinned_message_deleted",
        ),
    ],
)
def test_invalid_compact_shape_remains_malformed(event, payload, received, kind):
    diagnostics = []
    message = dispatch_event(
        pusher_frame(event, payload),
        received_timestamp=received,
        record_diagnostic=diagnostics.append,
    )
    assert message is None
    assert diagnostics == ["malformed_event_count", f"malformed_event_type:{kind}"]


@pytest.mark.parametrize(
    "frame",
    [
        {"event": CHAT_MESSAGE_EVENT, "data": "{not valid json"},
        pusher_frame(CHAT_MESSAGE_EVENT, {"content": "x"}),
        *[
            pusher_frame(event, {})
            for event in [
                PUSHER_CONNECTION_ESTABLISHED,
                PUSHER_SUBSCRIPTION_SUCCEEDED,
                PUSHER_PING,
            ]
        ],
    ],
)
def test_malformed_chat_and_control_frames_are_skipped(frame):
    assert dispatch_event(frame) is None


def test_pusher_error_is_captured_before_raise(captured):
    with pytest.raises(KickError):
        dispatch_event(pusher_frame(PUSHER_ERROR, {"message": "bad"}))
    assert captured[0][0][0] == "kick-pusher-error"
    assert captured[0][1]["sample_limit"] == 10


def test_unknown_event_is_captured_and_skipped(captured):
    frame = pusher_frame("App\\Events\\FutureEvent", {"future": True})
    assert dispatch_event(frame) is None
    assert captured[0][0] == (
        "kick-unknown-event-FutureEvent",
        {"raw": frame, "event_name": "App\\Events\\FutureEvent"},
    )
    assert captured[0][1] == {
        "sample_limit": 3,
        "sample_group": "kick-unknown-event",
        "group_limit": 10,
    }


def test_malformed_known_event_is_captured(captured):
    frame = pusher_frame(SUBSCRIPTION_EVENT, {})
    diagnostics = []
    assert dispatch_event(frame, record_diagnostic=diagnostics.append) is None
    assert diagnostics == ["malformed_event_count", "malformed_event_type:subscription"]
    assert captured[0][0][0] == "kick-malformed-event"
    assert captured[0][0][1]["raw"] == frame
    assert captured[0][0][1]["message_type"] == "subscription"


def test_frame_without_event_is_captured_and_skipped(captured):
    assert dispatch_event({"data": "{}"}) is None
    assert captured[0][0][0] == "kick-unknown-event"
    assert captured[0][0][1]["reason"] == "missing or non-string event name"


def test_unknown_event_capture_is_sanitized_on_disk(sample_dir):
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


def test_unknown_event_capture_is_bounded(sample_dir, caplog):
    for index in range(12):
        dispatch_event(pusher_frame(f"App\\Events\\Future{index}", {"index": index}))
    assert len(list(sample_dir.glob("kick-unknown-event-*.json"))) == 10
    assert "Debug sample limit reached" in caplog.text


def test_unknown_event_capture_isolated_by_event_name(sample_dir):
    for index in range(5):
        dispatch_event(pusher_frame("App\\Events\\NoisyEvent", {"index": index}))
    dispatch_event(pusher_frame("App\\Events\\DifferentEvent", {"value": 1}))
    assert len(list(sample_dir.glob("kick-unknown-event-noisyevent-*.json"))) == 3
    assert len(list(sample_dir.glob("kick-unknown-event-differentevent-*.json"))) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"host_username": " "},
        {"host_username": 1},
        {"number_viewers": True},
        {"number_viewers": -1},
        {"number_viewers": "1044"},
        {"chatroom_id": 0},
        {"chatroom_id": True},
        {"sender": {}},
        {"metadata": {}},
        {"id": ""},
        None,
    ],
)
def test_compact_host_does_not_repair_invalid_fields(overrides):
    data = (
        []
        if overrides is None
        else load_fixture("stream_host_event_compact.json") | overrides
    )
    message = dispatch_event(
        pusher_frame(STREAM_HOST_EVENT, data), received_timestamp=123
    )
    assert message is None


def test_existing_wrapped_host_is_unchanged_with_receive_time():
    frame = pusher_frame(STREAM_HOST_EVENT, load_fixture("stream_host_event.json"))
    assert dispatch_event(frame, received_timestamp=123) == dispatch_event(frame)

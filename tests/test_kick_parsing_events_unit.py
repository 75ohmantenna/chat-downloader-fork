# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_ERROR,
    PUSHER_PING,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.errors import KickError
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture, pusher_frame


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


def test_malformed_nested_json_is_skipped() -> None:
    frame = {"event": CHAT_MESSAGE_EVENT, "data": "{not valid json"}
    assert dispatch_event(frame) is None


def test_unparseable_chat_payload_is_skipped() -> None:
    # Valid JSON, but missing the required id.
    assert dispatch_event(pusher_frame(CHAT_MESSAGE_EVENT, {"content": "x"})) is None


def test_pusher_error_raises() -> None:
    with pytest.raises(KickError):
        dispatch_event(pusher_frame(PUSHER_ERROR, {"message": "bad"}))


@pytest.mark.parametrize(
    "event",
    [PUSHER_CONNECTION_ESTABLISHED, PUSHER_SUBSCRIPTION_SUCCEEDED, PUSHER_PING],
)
def test_control_events_are_ignored(event: str) -> None:
    assert dispatch_event(pusher_frame(event, {})) is None


def test_unknown_event_is_skipped() -> None:
    assert dispatch_event(pusher_frame("App\\Events\\SubscriptionEvent", {})) is None


def test_frame_without_event_is_skipped() -> None:
    assert dispatch_event({"data": "{}"}) is None

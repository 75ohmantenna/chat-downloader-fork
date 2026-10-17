# SPDX-License-Identifier: MIT

"""Recorded supplemental subscription feeds through live dispatch and output."""

from __future__ import annotations

import json
from collections import Counter
from contextlib import closing

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.writers import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.sites.kick.capture_inspection import inspect_capture
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import collect_live_chat, live_clock, load_fixture

_TYPES = (
    "chat_message_sent",
    "channel_subscription",
    "subscriber_count_updated",
    "activity_feed",
)
_TIME = 1792201627000000


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("canonical_first", [False, True])
def test_recorded_subscription_feeds_keep_details_and_one_subscription(
    tmp_path, encoded, canonical_first
):
    fixture = load_fixture("subscription_activity_recorded.json")
    frames = [event["frame"] for event in fixture["events"]]
    payloads = [json.loads(frame["data"]) for frame in frames]
    if not encoded:
        for frame, payload in zip(frames, payloads, strict=True):
            frame["data"] = payload
    frames.insert(
        0 if canonical_first else len(frames), fixture["canonical_subscription"]
    )
    with live_clock(return_value=_TIME * 1000):
        chat, messages = collect_live_chat([frames], message_groups=["all"])

    assert Counter(item["message_type"] for item in messages) == dict.fromkeys(
        (*_TYPES, "subscription"), 1
    )
    assert len({item["message_id"] for item in messages}) == 5
    assert chat.diagnostics["parsed_event_count"] == 5
    assert chat.diagnostics["unsupported_event_count"] == 0
    assert chat.diagnostics["malformed_event_count"] == 0
    supplemental = [item for item in messages if item["message_type"] != "subscription"]
    for index, item in enumerate(supplemental):
        frame = fixture["events"][index]["frame"]
        assert item["metadata"] == {
            "data": payloads[index],
            "event_name": frame["event"],
            "channel": frame["channel"],
            "source": "websocket",
        }
        assert "timestamp" not in item
        assert item["received_timestamp"] >= _TIME

    expected_details = [
        "[chat message sent] User: SampleSubscriber Action: subscribe Months: 1",
        "[channel subscription] User: SampleSubscriber",
        "[subscriber count updated] Count: 2251 Last subscriber: SampleSubscriber",
        "[activity feed] Type: new_subscriber User: SampleSubscriber",
    ]
    formatter = ItemFormatter()
    for item, expected in zip(supplemental, expected_details, strict=True):
        assert formatter.format(item, "kick") == (
            "2026-10-17 01:47:07 [received] | " + expected
        )

    jsonl, txt = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    with (
        closing(JsonLinesContinuousWriter(str(jsonl))) as raw,
        closing(TextContinuousWriter(str(txt))) as text,
    ):
        for item in messages:
            raw.write(item)
            text.write(formatter.format(item, "kick"))
    parity = audit_capture(jsonl, txt, formatter=formatter, format_name="kick")
    assert not parity.failed
    assert parity.jsonl_records == parity.txt_lines == 5
    report = inspect_capture(
        jsonl,
        run_summary={
            "success": True,
            "message_count": 5,
            "message_type_counts": dict.fromkeys((*_TYPES, "subscription"), 1),
            "provider_diagnostics": chat.diagnostics,
        },
    )
    assert report["status"] == "ok"
    assert report["issues"] == {}
    assert report["frame_accounting"]["unaccounted_frames"] == 0


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (["messages"], []),
        (["subscriptions"], ["subscription"]),
        (["activity"], ["chat_message_sent", "channel_subscription", "activity_feed"]),
        (["channel"], ["subscriber_count_updated"]),
    ],
)
def test_subscription_activity_respects_group_selection(groups, expected):
    fixture = load_fixture("subscription_activity_recorded.json")
    frames = [fixture["canonical_subscription"]]
    frames.extend(event["frame"] for event in fixture["events"])
    _, messages = collect_live_chat([frames], message_groups=groups)
    assert [item["message_type"] for item in messages] == expected


@pytest.mark.parametrize("index", range(4))
def test_supplemental_event_requires_receive_time_and_decodable_data(index):
    frame = load_fixture("subscription_activity_recorded.json")["events"][index][
        "frame"
    ]
    diagnostics = []
    assert dispatch_event(frame, record_diagnostic=diagnostics.append) is None
    assert diagnostics == [
        "malformed_event_count",
        "malformed_event_type:" + _TYPES[index],
    ]
    diagnostics.clear()
    frame["data"] = "{"
    assert (
        dispatch_event(
            frame, received_timestamp=_TIME, record_diagnostic=diagnostics.append
        )
        is None
    )
    assert diagnostics == [
        "malformed_event_count",
        "malformed_event_type:" + _TYPES[index],
    ]


@pytest.mark.parametrize("kind", _TYPES)
def test_supplemental_format_without_optional_details_keeps_label(kind):
    assert (
        ItemFormatter().format(
            {"message_type": kind, "message": kind.replace("_", " ")}, "kick"
        )
        == f"[{kind.replace('_', ' ')}]"
    )


def test_subscriber_count_format_keeps_zero():
    assert (
        ItemFormatter().format(
            {
                "message_type": "subscriber_count_updated",
                "message": "subscriber count updated",
                "metadata": {"data": {"count": 0}},
            },
            "kick",
        )
        == "[subscriber count updated] Count: 0"
    )


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "12", "", None, [], {}])
@pytest.mark.parametrize("index", [0, 2])
def test_invalid_activity_counts_are_preserved_but_not_rendered(index, value):
    frame = load_fixture("subscription_activity_recorded.json")["events"][index][
        "frame"
    ]
    payload = json.loads(frame["data"])
    if index == 0:
        payload["message"]["months_subscribed"] = value
        label = " Months:"
    else:
        payload["count"] = value
        label = " Count:"
    frame["data"] = json.dumps(payload)
    item = dispatch_event(frame, received_timestamp=_TIME)
    assert item["metadata"]["data"] == payload
    assert label not in ItemFormatter().format(item, "kick")


@pytest.mark.parametrize("index", range(4))
def test_activity_text_escapes_forged_lines_and_surrogates(tmp_path, index):
    frame = load_fixture("subscription_activity_recorded.json")["events"][index][
        "frame"
    ]
    payload = json.loads(frame["data"])
    forged = "Subscriber\nForged\r\u2028\u2029\x1b[31m\ud800"
    if index == 0:
        payload["user"]["username"] = forged
    elif index == 2:
        payload["last_subscriber"] = forged
    else:
        payload["username"] = forged
    frame["data"] = json.dumps(payload)
    item = dispatch_event(frame, received_timestamp=_TIME)
    formatter = ItemFormatter()
    rendered = formatter.format(item, "kick")
    assert "\\nForged\\r\\u2028\\u2029" in rendered
    assert "\\ud800" in rendered
    assert not any(char in rendered for char in "\n\r\u2028\u2029\x1b\ud800")
    assert item["metadata"]["data"] == payload
    jsonl, txt = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    with (
        closing(JsonLinesContinuousWriter(str(jsonl))) as raw,
        closing(TextContinuousWriter(str(txt))) as text,
    ):
        raw.write(item)
        text.write(rendered)
    parity = audit_capture(jsonl, txt, formatter=formatter, format_name="kick")
    assert not parity.failed
    assert parity.jsonl_records == parity.txt_lines == 1

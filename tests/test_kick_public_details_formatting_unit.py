# SPDX-License-Identifier: MIT

"""Recorded and adversarial public-event summaries through dispatch and output."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.writers import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture


def render(payload, kind="kicks_leaderboard_updated"):
    return ItemFormatter().format(
        {
            "message_type": kind,
            "message": kind.replace("_", " "),
            "metadata": {"data": payload},
        },
        "kick",
    )


@pytest.mark.parametrize("encoded", [False, True])
def test_recorded_public_details_preserve_payloads_and_exact_output_parity(
    tmp_path, encoded
):
    fixture = load_fixture("public_event_details_recorded.json")
    formatter = ItemFormatter()
    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    expected = [
        (
            "2026-10-04 14:41:23 [received] | [kicks leaderboard updated]"
            " | Weekly: User1 (20,000 Kicks), User2 (11,000 Kicks),"
            " User3 (11,000 Kicks)"
            " | Monthly: User6 (10,000 Kicks), User3 (10,000 Kicks),"
            " User1 (10,000 Kicks)"
            " | Lifetime: User12 (1,320,000 Kicks), User6 (682,100 Kicks),"
            " User13 (670,000 Kicks)"
        ),
        (
            "2026-10-04 14:41:24 [received] | [goal progress updated]"
            " | Followers: 188,912 / 200,000 | Status: active"
        ),
    ]
    with (
        closing(JsonLinesContinuousWriter(str(jsonl))) as raw_writer,
        closing(TextContinuousWriter(str(txt))) as text_writer,
    ):
        for event, text in zip(fixture["events"], expected, strict=True):
            frame = deepcopy(event["frame"])
            payload = deepcopy(frame["data"])
            if encoded:
                frame["data"] = json.dumps(payload)
            item = dispatch_event(frame, received_timestamp=event["received_timestamp"])
            assert item is not None
            rendered = formatter.format(item, "kick")
            assert rendered == text
            assert item["metadata"]["data"] == payload
            assert frame["data"] == (json.dumps(payload) if encoded else payload)
            raw_writer.write(item)
            text_writer.write(rendered)
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="kick")
    assert not stats.failed
    assert stats.jsonl_records == stats.txt_lines == 2


@pytest.mark.parametrize("enabled", [False, None, "true", 1, {}, []])
def test_leaderboard_requires_explicit_boolean_enabled_flag(enabled):
    assert (
        render(
            {
                "gifts_week_enabled": enabled,
                "gifts_week": [{"username": "Hidden", "quantity": 50}],
            }
        )
        == "[kicks leaderboard updated]"
    )


@pytest.mark.parametrize("payload", [None, [], "invalid", True, {}])
@pytest.mark.parametrize("kind", ["kicks_leaderboard_updated", "goal_progress_updated"])
def test_unusable_payload_keeps_event_label(payload, kind):
    assert render(payload, kind) == f"[{kind.replace('_', ' ')}]"


def test_leaderboard_empty_period_and_gift_units():
    assert render({"gifts_month_enabled": True, "gifts_month": []}) == (
        "[kicks leaderboard updated] | Monthly: empty"
    )
    assert render(
        {
            "gifts_week_enabled": True,
            "gifts_week": [{"username": "Gifter", "quantity": 3}],
        },
        "gifts_leaderboard_updated",
    ) == ("[gifts leaderboard updated] | Weekly: Gifter (3 gifts)")


@pytest.mark.parametrize("entries", [None, {}, "invalid", [None, {}, True]])
def test_invalid_entries_do_not_claim_empty_or_invent_leaders(entries):
    assert render({"gifts_week_enabled": True, "gifts_week": entries}) == (
        "[kicks leaderboard updated]"
    )


@pytest.mark.parametrize("quantity", [True, False, -1, "100", 1.5, None, {}])
def test_invalid_leaderboard_quantity_is_omitted(quantity):
    assert (
        render(
            {
                "gifts_week_enabled": True,
                "gifts_week": [
                    {"username": "Invalid", "quantity": quantity},
                    {"username": "Valid", "quantity": 0},
                ],
            }
        )
        == "[kicks leaderboard updated] | Weekly: Valid (0 Kicks)"
    )


def test_leaderboard_limits_slots_preserves_order_and_escapes_user_text():
    payload = {
        "gifts_week_enabled": True,
        "gifts_week": [
            {"username": "First\nForged\x1b\ud800", "quantity": 1},
            {"username": " ", "quantity": 999},
            {"username": "Third", "quantity": 10},
            {"username": "Fourth", "quantity": 9999},
        ],
    }
    original = deepcopy(payload)
    assert render(payload) == (
        "[kicks leaderboard updated] | Weekly: First\\nForged\\ud800 (1 Kicks),"
        " Third (10 Kicks)"
    )
    assert payload == original


@pytest.mark.parametrize(
    ("payload", "suffix"),
    [
        ({"current_value": 0, "target_value": 0}, " | Progress: 0 / 0"),
        ({"current_value": 5}, " | Progress: 5"),
        ({"target_value": 1000}, " | Progress: target 1,000"),
        ({"status": "achieved"}, " | Status: achieved"),
        ({"current_value": 110, "target_value": 100}, " | Progress: 110 / 100"),
        ({"current_value": True, "target_value": -1, "status": False}, ""),
        ({"current_value": 1.5, "target_value": "100"}, ""),
        ({"type": {}, "status": " "}, ""),
        ({"type": "new_goal", "current_value": 1}, " | New goal: 1"),
        (
            {
                "type": "followers\nForged",
                "current_value": 1,
                "status": "active\r\nFake\x1b",
            },
            r" | Followers\nforged: 1 | Status: active\r\nFake",
        ),
    ],
)
@pytest.mark.parametrize(
    "kind",
    [
        "goal_created",
        "goal_updated",
        "goal_progress_updated",
        "goal_achieved",
        "goal_canceled",
    ],
)
def test_goal_partial_and_adversarial_fields(payload, suffix, kind):
    original = deepcopy(payload)
    assert render(payload, kind) == f"[{kind.replace('_', ' ')}]{suffix}"
    assert payload == original

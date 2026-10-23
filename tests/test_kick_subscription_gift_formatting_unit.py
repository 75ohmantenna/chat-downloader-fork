# SPDX-License-Identifier: MIT

"""Recorded subscription gifts and adversarial ranking output contracts."""

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


@pytest.mark.parametrize("encoded", [False, True])
def test_recorded_subscription_gifts_preserve_payloads_and_output_parity(
    tmp_path, encoded
):
    fixture = load_fixture("subscription_gifts_recorded.json")
    formatter = ItemFormatter()
    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    expected = [
        (
            "2026-10-15 01:32:21 [received] | [gifts leaderboard updated]"
            " | Weekly: User7 (30 gifts), User21 (26 gifts), User20 (25 gifts)"
            " | Monthly: User11 (281 gifts), User12 (150 gifts), User13 (150 gifts)"
            " | Overall: User1 (5,330 gifts), User2 (5,148 gifts), User3 (5,134 gifts)"
        ),
        (
            "2026-10-15 01:32:21 [received] | [Gifted subscriptions] User3"
            " — Gifted subs: 25"
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
            # The live service supplies receive time for non-public parser results.
            item.setdefault("received_timestamp", event["received_timestamp"])
            original = deepcopy(item)
            rendered = formatter.format(item, "kick")
            assert rendered == text
            metadata = item["metadata"]
            assert metadata.get("kick_event", metadata)["data"] == payload
            assert frame["data"] == (json.dumps(payload) if encoded else payload)
            assert item == original
            raw_writer.write(item)
            text_writer.write(rendered)
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="kick")
    assert not stats.failed
    assert stats.jsonl_records == stats.txt_lines == 2


@pytest.mark.parametrize(
    ("quantity", "suffix"),
    [
        (1, " — Gifted subs: 1"),
        (25, " — Gifted subs: 25"),
        (0, " — Gifted subs: 0"),
        ("5", " — Gifted subs: 5"),
        (None, ""),
        (True, ""),
        (False, ""),
        (-1, ""),
        (1.5, ""),
        ("unknown", ""),
        ("\u0665", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_gift_counts_keep_author_message_and_timestamp(quantity, suffix):
    item = {
        "message_type": "gifted_subscriptions",
        "author": {"display_name": "Gifter"},
        "message": "Enjoy!",
        "timestamp": 1_577_836_800_000_000,
        "metadata": {"quantity": quantity},
    }
    assert ItemFormatter().format(item, "kick") == (
        f"2020-01-01 00:00:00 | [Gifted subscriptions] Gifter{suffix} — Enjoy!"
    )


def test_chunked_gift_notice_uses_recipient_count_not_batch_or_cumulative_total():
    frame = load_fixture("public_realtime_contracts.json")["events"][2]
    item = dispatch_event(frame, received_timestamp=100)
    assert ItemFormatter().format(item, "kick") == (
        "[Gifted subscriptions] gifter — Gifted subs: 1"
    )
    assert item["metadata"]["kick_event"]["data"]["gifted_total"] == 50
    assert item["metadata"]["kick_event"]["data"]["gifter_total"] == 100


@pytest.mark.parametrize(
    ("payload", "suffix"),
    [
        (None, ""),
        ([], ""),
        ({}, ""),
        ({"weekly_leaderboard": []}, " | Weekly: empty"),
        ({"monthly_leaderboard": []}, " | Monthly: empty"),
        ({"leaderboard": []}, " | Overall: empty"),
        ({"weekly_leaderboard": None}, ""),
        ({"weekly_leaderboard": {}}, ""),
        ({"weekly_leaderboard": [None, {}, True]}, ""),
        (
            {
                "weekly_leaderboard": [
                    {"username": "Bad", "quantity": True},
                    {"username": "Bad", "quantity": -1},
                    {"username": "Bad", "quantity": "5"},
                    {"username": "Fourth", "quantity": 50},
                ]
            },
            "",
        ),
        (
            {
                "weekly_leaderboard": [
                    {"username": "First\nForged\x1b\ud800", "quantity": 0},
                    {"username": " ", "quantity": 900},
                    {"username": "Third", "quantity": 1000},
                    {"username": "Fourth", "quantity": 2000},
                ]
            },
            r" | Weekly: First\nForged\ud800 (0 gifts), Third (1,000 gifts)",
        ),
        (
            {
                "weekly_leaderboard": [],
                "gifts_week_enabled": True,
                "gifts_week": [{"username": "Stale", "quantity": 50}],
            },
            " | Weekly: empty",
        ),
        (
            {
                "weekly_leaderboard": None,
                "gifts_week_enabled": True,
                "gifts_week": [{"username": "Stale", "quantity": 50}],
            },
            "",
        ),
        (
            {
                "weekly_leaderboard": [],
                "gifts_month_enabled": True,
                "gifts_month": [],
                "gifts_lifetime_enabled": True,
                "gifts_lifetime": [],
            },
            " | Weekly: empty | Monthly: empty | Lifetime: empty",
        ),
    ],
)
def test_subscription_rankings_handle_partial_and_adversarial_payloads(payload, suffix):
    item = {
        "message_type": "gifts_leaderboard_updated",
        "message": "gifts leaderboard updated",
        "metadata": {"data": payload},
    }
    original = deepcopy(item)
    assert (
        ItemFormatter().format(item, "kick") == f"[gifts leaderboard updated]{suffix}"
    )
    assert item == original
    item.update(
        message_type="kicks_leaderboard_updated", message="kicks leaderboard updated"
    )
    # Subscription lists cannot bypass Kicks enable flags or supply Kicks rankings.
    kicks = ItemFormatter().format(item, "kick")
    assert "Overall:" not in kicks
    assert "Forged" not in kicks
    assert "Fourth" not in kicks

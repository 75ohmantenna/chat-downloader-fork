# SPDX-License-Identifier: MIT

import json
from pathlib import Path

_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "youtube" / "live_events"
)


def _load_fixture(name: str) -> list[dict]:
    payload = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def test_shu_live_event_fixture_covers_paid_membership_and_ticker_paths() -> (
    None
):
    items = _load_fixture("youtube-shu-pokopia-10m-events.json")

    message_types = {item["message_type"] for item in items}
    assert message_types == {
        "banner",
        "membership_item",
        "paid_message",
        "ticker_paid_message_item",
        "ticker_sponsor_item",
        "viewer_engagement_message",
    }

    paid = next(
        item for item in items if item["message_type"] == "paid_message"
    )
    assert paid["money"]["currency"] == "JPY"
    assert paid["money"]["text"] == "\u00a55,000"
    assert paid["time_text"] == "9:44"

    ticker_paid = next(
        item
        for item in items
        if item["message_type"] == "ticker_paid_message_item"
    )
    assert ticker_paid["ticker_duration"] == 1800
    assert ticker_paid["message_id"] == paid["message_id"]

    sponsor_ticker = next(
        item for item in items if item["message_type"] == "ticker_sponsor_item"
    )
    assert sponsor_ticker["ticker_duration"] == 120
    assert sponsor_ticker["time_text"] == "4:00"

    null_membership = next(
        item
        for item in items
        if item["message_type"] == "membership_item" and item["message"] is None
    )
    assert null_membership["header_secondary_text"] == "Welcome to Yaminions!"


def test_crimson_live_event_fixture_covers_banner_moderation_and_engagement() -> (  # noqa: E501
    None
):
    items = _load_fixture("youtube-shapy-crimson-desert-10m-events.json")

    assert [item["message_type"] for item in items] == [
        "banner",
        "banner",
        "ban_user",
        "viewer_engagement_message",
    ]

    rich_banner = items[1]
    assert rich_banner["author"]["name"] == "@shapyxavier"
    assert "igamers.com.tw" in rich_banner["message"]

    ban = items[2]
    assert ban["action_type"] == "remove_chat_item"
    assert ban["target_message_id"].startswith("ChwK")
    assert ban["message"] is None

    engagement = items[3]
    assert engagement["icon"] == "YOUTUBE_ROUND"
    assert "community guidelines" in engagement["message"]

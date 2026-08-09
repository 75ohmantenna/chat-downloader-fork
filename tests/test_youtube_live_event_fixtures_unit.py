# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuations import (
    parse_continuation_response,
)
from chat_downloader.sites.youtube.message_pipeline import (
    process_pipeline_action,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "youtube" / "live_events"


def _load_fixture(name: str) -> list[dict]:
    payload = json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def _load_payload(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_shu_live_event_fixture_covers_paid_membership_and_ticker_paths() -> None:
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

    paid = next(item for item in items if item["message_type"] == "paid_message")
    assert paid["money"]["currency"] == "JPY"
    assert paid["money"]["text"] == "\u00a55,000"
    assert paid["time_text"] == "9:44"

    ticker_paid = next(
        item for item in items if item["message_type"] == "ticker_paid_message_item"
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


def test_crimson_live_event_fixture_covers_banner_moderation_and_engagement() -> None:
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


def test_IzopCEgh2G8_first_live_poll_fixture_parses_text_and_summary_banner() -> None:
    payload = _load_payload("youtube-IzopCEgh2G8-live-chat-first.json")
    result = parse_continuation_response(payload)
    all_filter = MessageFilter(
        _MESSAGE_GROUPS,
        groups_to_add=["all"],
        types_to_add=None,
    )

    parsed = []
    for action in result.actions:
        pipeline_result = process_pipeline_action(
            json.loads(json.dumps(action)),
            0,
            all_filter,
            None,
        )
        if pipeline_result.message:
            parsed.append(pipeline_result.message)

    message_types = {item["message_type"] for item in parsed}
    assert result.next_continuation
    assert result.timeout_ms == 10000
    assert "text_message" in message_types
    assert "viewer_engagement_message" in message_types
    assert "banner_chat_summary" in message_types

    summary = next(
        item for item in parsed if item["message_type"] == "banner_chat_summary"
    )
    assert summary["summary_id"].startswith("IzopCEgh2G8_")
    assert "Chat summary" in summary["message"]


def test_mobile_element_chat_fixture_parses_through_real_pipeline() -> None:
    payload = _load_payload("youtube-CH0uI-v2Cbc-mobile-element-chat.json")
    result = parse_continuation_response(payload)
    messages_filter = MessageFilter(
        _MESSAGE_GROUPS,
        groups_to_add=["messages"],
        types_to_add=None,
    )

    pipeline_result = process_pipeline_action(
        result.actions[0],
        0,
        messages_filter,
        None,
    )

    assert result.next_continuation == "next-mobile-token"
    assert result.timeout_ms == 5000
    assert pipeline_result.disposition == "yield"
    assert pipeline_result.message == {
        "action_type": "add_chat_item",
        "author": {
            "badges": [
                {
                    "title": "Member",
                    "icons": [
                        {"url": "https://img.example/member", "id": "source"},
                        {
                            "url": "https://img.example/member=s16",
                            "width": 16,
                            "height": 16,
                            "id": "16x16",
                        },
                    ],
                },
            ],
            "id": "UC-sanitized-author",
            "images": [
                {"url": "https://img.example/avatar", "id": "source"},
                {
                    "url": "https://img.example/avatar=s32",
                    "width": 32,
                    "height": 32,
                    "id": "32x32",
                },
            ],
            "is_sponsor": True,
            "name": "@fixture-viewer",
        },
        "message": "Fixture mobile chat message",
        "message_id": "sanitized-message-id",
        "message_type": "text_message",
        "timestamp": 1784402103176311,
    }


def test_jewels_gift_fixture_parses_through_real_pipeline() -> None:
    payload = _load_payload("youtube-MLmJY7SeISw-jewels-gift-event.json")
    result = parse_continuation_response(payload)
    superchat_filter = MessageFilter(
        _MESSAGE_GROUPS,
        groups_to_add=["superchat"],
        types_to_add=None,
    )

    pipeline_result = process_pipeline_action(
        result.actions[0],
        0,
        superchat_filter,
        None,
    )

    assert result.next_continuation == "NEXT_GIFT_FIXTURE_TOKEN"
    assert pipeline_result.disposition == "yield"
    assert pipeline_result.message is not None
    assert pipeline_result.message["action_type"] == (
        "update_or_add_interactivity_widget"
    )
    assert pipeline_result.message["message_type"] == "gift_message_view_model"
    assert pipeline_result.message["message_id"] == "gift-event-001"
    assert pipeline_result.message["message"] == "Sent a gift"
    assert pipeline_result.message["combo_count"] == 3
    assert pipeline_result.message["gift_image_a11y_label"] == (
        "@gift_sender sent a gift, Image of a star"
    )
    assert pipeline_result.message["gift_images"][1] == {
        "url": "https://example.com/gift-80.png",
        "width": 80,
        "height": 80,
        "id": "80x80",
    }
    assert pipeline_result.message["author"]["id"] == "UC-gift-sender"
    assert pipeline_result.message["author"]["name"] == "@gift_sender"
    assert pipeline_result.message["author"]["images"][1] == {
        "url": "https://example.com/avatar-32.png",
        "width": 32,
        "height": 32,
        "id": "32x32",
    }

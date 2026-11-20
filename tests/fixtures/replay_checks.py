# SPDX-License-Identifier: MIT

"""Assertions for bounded provider network scenarios."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from chat_downloader.sites.models import Chat


def assert_live_delivery(
    chat: Chat, messages: list[dict[str, Any]], *, allowed_types: list[str]
) -> None:
    assert_message_limit(chat, messages, limit=1)
    message_type = messages[0]["message_type"]
    assert message_type in allowed_types, (
        f"Unexpected live message type: {message_type!r}."
    )


def assert_message_limit(
    chat: Chat, messages: list[dict[str, Any]], *, limit: int
) -> None:
    assert len(messages) == limit, (
        f"Expected {limit} messages, received {len(messages)}."
    )
    termination = chat.diagnostics.get("termination_reason")
    assert termination == "message_limit", (
        f"Expected message_limit termination, received {termination!r}."
    )


def assert_timed_window(
    messages: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    required_types: tuple[str, ...] = (),
) -> None:
    offsets = [
        message["time_in_seconds"]
        for message in messages
        if "time_in_seconds" in message
    ]
    assert offsets, "Replay should contain at least one timed record."
    for message in messages:
        if message["message_type"] in required_types:
            assert "time_in_seconds" in message, (
                f"Replay {message['message_type']} is missing its offset."
            )
    for offset in offsets:
        assert type(offset) in (int, float), f"Invalid replay offset {offset!r}."
        assert start <= offset <= end, (
            f"Replay offset {offset} is outside the requested window [{start}, {end}]."
        )


def _check_text_timing(chat: Chat, message: dict[str, Any]) -> None:
    timestamp = message.get("timestamp")
    if timestamp is not None:
        assert type(timestamp) is int, "Invalid source timestamp."
        assert timestamp >= 0, "Invalid source timestamp."
        return

    assert isinstance(chat.site, YouTubeChatDownloader), "Missing source timestamp."
    if chat.status not in REPLAY_STATUSES:
        received = message.get("received_timestamp")
        assert type(received) is int, "Missing valid receive time."
        assert received >= 0, "Invalid receive time."
        return

    offset = message.get("time_in_seconds")
    assert isinstance(offset, (int, float)), "Missing provider replay offset."
    assert not isinstance(offset, bool), "Invalid provider replay offset."
    assert not isinstance(offset, float) or math.isfinite(offset), (
        "Invalid provider replay offset."
    )
    timing = message.get("timing")
    assert isinstance(timing, dict), "Missing replay timing provenance."
    assert timing.get("offset_source") == "provider_replay"
    assert timing.get("absolute_timestamp_available") is False
    assert timing.get("zero_offset_ambiguous") is (offset == 0)


def assert_message_contract(chat: Chat, messages: list[dict[str, Any]]) -> None:
    for message in messages:
        assert isinstance(message.get("message_type"), str)
        if message["message_type"] == "text_message":
            assert message.get("message_id")
            _check_text_timing(chat, message)
            assert isinstance(message.get("author"), dict)
            assert isinstance(message.get("message"), str)

# SPDX-License-Identifier: MIT

"""Assertions for bounded network replay scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chat_downloader.sites.models import Chat


def assert_message_limit(
    chat: Chat, messages: list[dict[str, Any]], *, limit: int
) -> None:
    assert len(messages) == limit, (
        f"Expected {limit} messages, received {len(messages)}."
    )
    assert chat.diagnostics.get("termination_reason") == "message_limit"


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

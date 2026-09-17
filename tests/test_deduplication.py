# SPDX-License-Identifier: MIT

"""Formatted deduplication never removes messages from the raw chat stream."""

from __future__ import annotations

import pytest

from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites._message_dedup import _FormattedMessageDeduplicator
from chat_downloader.sites.models import Chat


@pytest.mark.parametrize("message_id", [None, "", 123])
def test_formatted_deduplicator_ignores_unusable_ids(message_id) -> None:
    message = {"message_type": "paid_message"}
    if message_id is not None:
        message["message_id"] = message_id
    deduplicator = _FormattedMessageDeduplicator()
    assert deduplicator.should_emit(message)
    assert deduplicator.should_emit(message)


@pytest.mark.parametrize(
    ("events", "limit", "retained"),
    [
        ([(primary, "one"), (ticker, "one")], None, [0])
        for primary, ticker in [
            ("paid_message", "ticker_paid_message_item"),
            ("paid_sticker", "ticker_paid_sticker_item"),
            ("membership_item", "ticker_sponsor_item"),
        ]
    ]
    + [
        ([("paid_message", "one"), ("paid_message", "two")], None, [0, 1]),
        ([("text_message", "one"), ("text_message", "one")], None, [0, 1]),
        ([("paid_message", None), ("paid_message", None)], None, [0, 1]),
        (
            [
                ("paid_message", "one"),
                ("paid_message", "two"),
                ("ticker_paid_message_item", "one"),
            ],
            1,
            [0, 1, 2],
        ),
    ],
)
def test_formatted_output_deduplicates_only_eligible_cached_ids(
    tmp_path, events, limit, retained
) -> None:
    messages = []
    for message_type, message_id in events:
        message = {"message_type": message_type}
        if message_id is not None:
            message["message_id"] = message_id
        messages.append(message)

    def render(message):
        return f"{message['message_type']}: {message.get('message_id', 'no-id')}"

    chat = Chat(iter(messages), max_seen_message_ids=limit)
    chat.set_formatter(render)
    output = tmp_path / "chat.txt"
    chat.attach_writer(
        ContinuousWriter(str(output), overwrite=True, lazy_initialise=True)
    )

    assert list(chat) == messages
    assert output.read_text().splitlines() == [render(messages[i]) for i in retained]

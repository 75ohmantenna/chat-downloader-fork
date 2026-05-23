# SPDX-License-Identifier: MIT

"""Tests for superchat message deduplication feature.

Tests that YouTube superchat messages that appear in both chat and ticker are
properly deduplicated when writing to formatted output.
"""

import os
import tempfile

from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import SUPERCHAT_DEDUP_TYPES, Chat


def test_superchat_dedup_types_constant() -> None:
    """Test that SUPERCHAT_DEDUP_TYPES contains expected message types."""
    expected_types = {
        "paid_message",
        "ticker_paid_message_item",
        "paid_sticker",
        "ticker_paid_sticker_item",
        "membership_item",
        "ticker_sponsor_item",
    }
    assert expected_types == SUPERCHAT_DEDUP_TYPES
    # Verify it's a frozenset (immutable)
    assert isinstance(SUPERCHAT_DEDUP_TYPES, frozenset)


def test_deduplication_in_formatted_output() -> None:
    """Test that duplicate superchat messages are not written to file."""
    # Create test messages - a paid_message and its ticker counterpart
    messages = [
        {
            "message_id": "msg123",
            "message_type": "paid_message",
            "message": "Test superchat",
            "author": {"name": "TestUser"},
            "time_in_seconds": 10,
            "time_text": "0:10",
        },
        {
            "message_id": "msg123",  # Same ID as above
            "message_type": "ticker_paid_message_item",
            "message": "Test superchat",
            "author": {"name": "TestUser"},
            "time_in_seconds": 10,
            "time_text": "0:10",
        },
    ]

    def message_generator():
        yield from messages

    # Create a Chat object with a simple format method
    chat = Chat(chat=message_generator(), title="Test", id="test123")

    # Override format method for testing
    def simple_format(item) -> str:
        return f"{item['message_type']}: {item['message_id']}"

    chat.set_formatter(simple_format)

    # Create a temp file for output
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        # Attach a writer with lazy initialization
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        # Consume the chat
        result_messages = list(chat)

        # Verify we got both messages from the generator
        assert len(result_messages) == 2

        # Read the output file
        with open(temp_file) as f:
            content = f.read()

        # Should only have ONE line (first message), not two
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1
        assert "paid_message: msg123" in lines[0]

    finally:
        # Clean up
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_no_deduplication_for_different_ids() -> None:
    """Test that messages with different IDs are not deduplicated."""
    messages = [
        {
            "message_id": "msg123",
            "message_type": "paid_message",
            "message": "First superchat",
            "time_text": "0:10",
        },
        {
            "message_id": "msg456",  # Different ID
            "message_type": "paid_message",
            "message": "Second superchat",
            "time_text": "0:20",
        },
    ]

    def message_generator():
        yield from messages

    chat = Chat(chat=message_generator(), title="Test", id="test123")

    def simple_format(item) -> str:
        return f"{item['message_type']}: {item['message_id']}"

    chat.set_formatter(simple_format)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        list(chat)  # Consume

        with open(temp_file) as f:
            content = f.read()

        # Should have TWO lines (both messages)
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_no_deduplication_for_non_superchat_types() -> None:
    """Test that non-superchat messages are never deduplicated."""
    messages = [
        {
            "message_id": "msg123",
            "message_type": "text_message",  # Not a superchat type
            "message": "Regular message",
            "time_text": "0:10",
        },
        {
            "message_id": "msg123",  # Same ID, but not a superchat type
            "message_type": "text_message",
            "message": "Another regular message",
            "time_text": "0:20",
        },
    ]

    def message_generator():
        yield from messages

    chat = Chat(chat=message_generator(), title="Test", id="test123")

    def simple_format(item) -> str:
        return f"{item['message_type']}: {item['message_id']}"

    chat.set_formatter(simple_format)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        list(chat)  # Consume

        with open(temp_file) as f:
            content = f.read()

        # Should have TWO lines (both messages, no deduplication)
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_deduplication_with_membership_and_ticker() -> None:
    """Test deduplication of membership_item and ticker_sponsor_item."""
    messages = [
        {
            "message_id": "member789",
            "message_type": "membership_item",
            "message": "New member!",
            "time_text": "0:30",
        },
        {
            "message_id": "member789",  # Same ID
            "message_type": "ticker_sponsor_item",
            "message": "New member!",
            "time_text": "0:30",
        },
    ]

    def message_generator():
        yield from messages

    chat = Chat(chat=message_generator(), title="Test", id="test123")

    def simple_format(item) -> str:
        return f"{item['message_type']}: {item['message_id']}"

    chat.set_formatter(simple_format)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        list(chat)  # Consume

        with open(temp_file) as f:
            content = f.read()

        # Should only have ONE line (deduplicated)
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1
        assert "membership_item: member789" in lines[0]

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_messages_without_id_not_deduplicated() -> None:
    """Test that messages without message_id are not deduplicated."""
    messages = [
        {
            # No message_id
            "message_type": "paid_message",
            "message": "Superchat without ID",
            "time_text": "0:10",
        },
        {
            # No message_id
            "message_type": "paid_message",
            "message": "Another superchat without ID",
            "time_text": "0:20",
        },
    ]

    def message_generator():
        yield from messages

    chat = Chat(chat=message_generator(), title="Test", id="test123")

    def simple_format(item) -> str:
        return f"{item['message_type']}: {item.get('message_id', 'no-id')}"

    chat.set_formatter(simple_format)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        list(chat)  # Consume

        with open(temp_file) as f:
            content = f.read()

        # Should have TWO lines (no deduplication without message_id)
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_superchat_dedup_cache_is_bounded() -> None:
    """Test that dedup cache evicts oldest IDs when configured with small
    max.
    """
    messages = [
        {
            "message_id": "msg1",
            "message_type": "paid_message",
            "message": "First superchat",
            "time_text": "0:10",
        },
        {
            "message_id": "msg2",
            "message_type": "paid_message",
            "message": "Second superchat",
            "time_text": "0:20",
        },
        {
            # msg1 was evicted from cache and should be emitted again
            "message_id": "msg1",
            "message_type": "ticker_paid_message_item",
            "message": "Duplicate of first superchat",
            "time_text": "0:30",
        },
    ]

    def message_generator():
        yield from messages

    chat = Chat(
        chat=message_generator(),
        title="Test",
        id="test123",
        max_seen_message_ids=1,
    )

    def simple_format(item) -> str:
        return f"{item['message_type']}: {item.get('message_id', 'no-id')}"

    chat.set_formatter(simple_format)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        temp_file = f.name

    try:
        writer = ContinuousWriter(
            temp_file, overwrite=True, lazy_initialise=True
        )
        chat.attach_writer(writer)

        list(chat)  # Consume

        with open(temp_file) as f:
            content = f.read()

        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 3
        assert any("paid_message: msg1" in line for line in lines)
        assert any("paid_message: msg2" in line for line in lines)
        assert any("ticker_paid_message_item: msg1" in line for line in lines)

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def test_superchat_dedup_cache_none_uses_default_limit() -> None:
    """Passing None should preserve default bounded-cache behavior."""
    chat = Chat(
        chat=iter(()), title="Test", id="test123", max_seen_message_ids=None
    )

    assert chat._seen_message_cache.limit > 0
    assert chat._register_seen_message_id("msg1")
    assert not chat._register_seen_message_id("msg1")

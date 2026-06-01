# SPDX-License-Identifier: MIT

"""Test multiple output formats feature."""

import json
import os

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.debugging import set_log_level

pytestmark = pytest.mark.network


@pytest.mark.network
def test_multiple_output_formats(tmp_path) -> None:
    """Test writing to multiple output files simultaneously."""
    set_log_level("warning")  # Reduce log noise during tests

    # Setup output file paths
    jsonl_output = str(tmp_path / "output.jsonl")
    txt_output = str(tmp_path / "output.txt")

    # Get a small number of messages from a YouTube VOD (past broadcast).
    # Using a VOD avoids the JSON->JSONL live-stream redirect.
    downloader = ChatDownloader()
    chat = downloader.get_chat(
        url="https://www.youtube.com/watch?v=wXspodtIxYU",
        max_messages=5,
        timeout=30,
        output=[jsonl_output, txt_output],
    )

    # Consume the chat iterator to trigger writes
    messages = list(chat)

    # Verify we got some messages
    assert len(messages) > 0, "Should retrieve at least one message"
    assert len(messages) <= 5, "Should not exceed max_messages"

    assert os.path.exists(jsonl_output), "JSONL output file should exist"
    assert os.path.exists(txt_output), "TXT output file should exist"

    # Verify JSONL output has correct number of lines
    with open(jsonl_output, encoding="utf-8") as f:
        jsonl_lines = f.readlines()
        assert len(jsonl_lines) == len(messages), (
            "JSONL should have same number of lines as messages"
        )
        # Verify each line is valid JSON
        for line in jsonl_lines:
            json.loads(line)  # Should not raise exception

    # Verify TXT output exists and has content
    with open(txt_output, encoding="utf-8") as f:
        txt_lines = f.readlines()
        assert len(txt_lines) > 0, "TXT output should have at least one line"


@pytest.mark.network
def test_single_output_still_works(tmp_path) -> None:
    """Test that single output (legacy behavior) still works."""
    set_log_level("warning")

    jsonl_output = str(tmp_path / "single_output.jsonl")

    downloader = ChatDownloader()
    chat = downloader.get_chat(
        url="https://www.youtube.com/watch?v=wXspodtIxYU",
        max_messages=3,
        timeout=30,
        output=jsonl_output,  # Single output, not a list
    )

    messages = list(chat)

    assert len(messages) > 0, "Should retrieve at least one message"
    assert os.path.exists(jsonl_output), "Single output file should be created"

    with open(jsonl_output, encoding="utf-8") as f:
        assert len(f.readlines()) == len(messages)

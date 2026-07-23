# SPDX-License-Identifier: MIT

"""Test multiple output formats feature."""

from __future__ import annotations

import json

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.debugging import set_log_level

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_replay,
    pytest.mark.timeout(90),
]


def test_multiple_output_formats(tmp_path) -> None:
    """Write one replay to JSONL and text outputs end to end."""
    set_log_level("warning")
    jsonl_output = tmp_path / "output.jsonl"
    txt_output = tmp_path / "output.txt"

    downloader = ChatDownloader()
    chat = None
    try:
        chat = downloader.get_chat(
            url="https://www.youtube.com/watch?v=wXspodtIxYU",
            max_messages=5,
            timeout=30,
            output=[str(jsonl_output), str(txt_output)],
        )
        messages = list(chat)
    finally:
        if chat is not None:
            chat.close()
        downloader.close()

    assert 0 < len(messages) <= 5
    jsonl_lines = jsonl_output.read_text(encoding="utf-8").splitlines()
    assert len(jsonl_lines) == len(messages)
    assert all(isinstance(json.loads(line), dict) for line in jsonl_lines)
    assert txt_output.read_text(encoding="utf-8").strip()

# SPDX-License-Identifier: MIT

"""Verify multiple output formats against the public message stream."""

from __future__ import annotations

import json
from contextlib import closing
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.fixtures.replay_checks import assert_message_contract, assert_message_limit

if TYPE_CHECKING:
    from pathlib import Path

    from chat_downloader.output.capture_parity import AuditStats
    from chat_downloader.utils.json_types import JSONDict

_VIDEO_URL = "https://www.youtube.com/watch?v=wXspodtIxYU"
_REPLAY_LIMIT = 10


def _assert_outputs(
    chat: Chat, messages: list[JSONDict], jsonl_output: Path, txt_output: Path
) -> AuditStats:
    with jsonl_output.open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    assert records == messages, "JSONL differs from the yielded messages"

    # Post-live DVR uses replay continuations and the live timestamp preset.
    format_name = "youtube_live_default" if chat.status == "post_live" else "youtube"
    stats = audit_capture(
        jsonl_output, txt_output, formatter=ItemFormatter(), format_name=format_name
    )
    assert not stats.failed, (
        f"TXT parity audit failed for {chat.status=} with {format_name=}: {stats}"
    )
    assert stats.comparison_complete
    assert not stats.jsonl_missing
    assert not stats.txt_missing
    assert stats.jsonl_records == stats.jsonl_lines == len(messages)
    assert stats.txt_lines == stats.expected_txt_lines > 0
    assert stats.txt_lines + stats.suppressed_duplicates == len(messages)
    return stats


@pytest.mark.network
@pytest.mark.network_replay
@pytest.mark.timeout(90)
def test_multiple_output_formats(tmp_path) -> None:
    """Write one replay to JSONL and text outputs end to end."""
    jsonl_output = tmp_path / "output.jsonl"
    txt_output = tmp_path / "output.txt"

    with closing(ChatDownloader(connect_timeout=5, read_timeout=10)) as downloader:
        with downloader.get_chat(
            url=_VIDEO_URL,
            max_messages=_REPLAY_LIMIT,
            timeout=30,
            max_attempts=2,
            interruptible_retry=False,
            retry_timeout=1,
            output=[str(jsonl_output), str(txt_output)],
        ) as chat:
            messages = list(chat)
        assert chat.write_error_count == 0

    assert type(chat.site) is YouTubeChatDownloader
    assert chat.diagnostics["chat_view"] == "Live chat replay"
    assert_message_limit(chat, messages, limit=_REPLAY_LIMIT)
    assert_message_contract(chat, messages)
    assert {message["message_type"] for message in messages} == {"text_message"}
    assert {message.get("action_type") for message in messages} == {"add_chat_item"}
    _assert_outputs(chat, messages, jsonl_output, txt_output)


@pytest.mark.parametrize(
    ("status", "time_prefix"),
    [("was_live", "0:05"), ("post_live", "12:00:00")],
)
@pytest.mark.parametrize(
    ("corrupt", "error"),
    [
        ("jsonl", "JSONL differs from the yielded messages"),
        ("txt", "TXT parity audit failed"),
    ],
)
def test_multiple_outputs_detect_corrupt_content(
    tmp_path, monkeypatch, status, time_prefix, corrupt, error
) -> None:
    """Exercise output checks with deterministic TXT suppression and corruption."""
    source = [
        {
            "message_id": "paid",
            "message_type": "paid_message",
            "message": "Thanks!",
        },
        {
            "message_id": "paid",
            "message_type": "ticker_paid_message_item",
            "message": "Thanks!",
        },
        {
            "message_id": "regular",
            "message_type": "text_message",
            "message": "Hello!",
            "time_text": "0:05",
            "timestamp": 43_200_000_000,
        },
    ]
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "get_chat_by_video_id",
        Mock(return_value=Chat(iter(source), status=status)),
    )
    jsonl_output = tmp_path / "output.jsonl"
    txt_output = tmp_path / "output.txt"

    with (
        closing(ChatDownloader()) as downloader,
        downloader.get_chat(
            url=_VIDEO_URL, output=[str(jsonl_output), str(txt_output)]
        ) as chat,
    ):
        messages = list(chat)

    assert chat.write_error_count == 0
    assert messages == source
    stats = _assert_outputs(chat, messages, jsonl_output, txt_output)
    assert stats.suppressed_duplicates == 1
    assert stats.txt_lines == 2
    text = txt_output.read_text(encoding="utf-8")
    assert text.splitlines()[-1] == f"{time_prefix} | : Hello!"

    if corrupt == "jsonl":
        # Retain the original line count and rendered text.
        records = [*messages[:-1], {**messages[-1], "message_id": "wrong"}]
        jsonl_output.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
    else:
        # Retain nonempty TXT and its original line count.
        txt_output.write_text(text.replace("Hello!", "Goodbye!"), encoding="utf-8")

    with pytest.raises(AssertionError, match=error):
        _assert_outputs(chat, messages, jsonl_output, txt_output)

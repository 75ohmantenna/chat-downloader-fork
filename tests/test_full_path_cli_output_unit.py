# SPDX-License-Identifier: MIT

"""Offline CLI → dispatch → Chat → output integration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

from chat_downloader.cli import main
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

_MESSAGES = [
    {"message_id": "m1", "message": "hello", "message_type": "text_message"},
    {"message_id": "m2", "message": "world", "message_type": "text_message"},
    {"message_id": "m3", "message": "again", "message_type": "text_message"},
]


def test_cli_full_path_writes_and_closes_output_file(tmp_path, monkeypatch) -> None:
    out_file = tmp_path / "chat.jsonl"

    class _FakeSite(BaseChatDownloader):
        _NAME = "fake.test"
        _VALID_URLS: ClassVar[dict[str, str]] = {
            "_get_chat_by_fake": r"https://fake\.test/(?P<id>\w+)"
        }

        def _get_chat_by_fake(self, match, request: ChatRequest) -> Chat:
            return Chat(
                (m for m in _MESSAGES),
                title="Fake Stream",
                id=match.group("id"),
                status="live",
            )

    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.get_all_sites",
        lambda: [_FakeSite],
    )

    main(
        [
            "https://fake.test/stream42",
            "--output",
            str(out_file),
            "--max_messages",
            "2",
            "--quiet",
        ]
    )

    lines = [
        json.loads(line)
        for line in out_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["message_id"] for record in lines] == ["m1", "m2"]
    assert all(record["message_type"] == "text_message" for record in lines)

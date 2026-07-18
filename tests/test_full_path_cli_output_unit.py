# SPDX-License-Identifier: MIT

"""End-to-end offline path: CLI args → dispatch → Chat → output file → close.

Unit tests cover each seam in isolation (arg parsing, ChatRequest construction,
site dispatch, chat_pipeline output wiring, writers, runner). This test wires
them together through the real ``cli.main`` entry point with a fake site, so a
regression in how the seams connect is caught even when each unit still passes.
"""

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
    seen_requests: list[ChatRequest] = []

    class _FakeSite(BaseChatDownloader):
        _NAME = "fake.test"
        _VALID_URLS: ClassVar[dict[str, str]] = {
            "_get_chat_by_fake": r"https://fake\.test/(?P<id>\w+)"
        }

        def _get_chat_by_fake(self, match, request: ChatRequest) -> Chat:
            # Capture the typed request the dispatch layer resolved and passed.
            seen_requests.append(request)
            return Chat(
                (m for m in _MESSAGES),
                title="Fake Stream",
                id=match.group("id"),
                status="live",
            )

    # Register the fake site in the real dispatch registry.
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.get_all_sites",
        lambda: [_FakeSite],
    )

    # Drive the real CLI entry point: parse argv → run → dispatch → output.
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

    # The typed request reached the site with CLI values applied.
    assert len(seen_requests) == 1
    assert seen_requests[0].url == "https://fake.test/stream42"
    assert seen_requests[0].max_messages == 2

    # Output file exists, is valid JSONL, honored --max-messages, and was
    # flushed/closed (a complete, parseable final line proves close ran).
    lines = [
        json.loads(line)
        for line in out_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["message_id"] for record in lines] == ["m1", "m2"]
    assert all(record["message_type"] == "text_message" for record in lines)

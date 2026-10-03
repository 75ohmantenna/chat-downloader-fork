# SPDX-License-Identifier: MIT

"""Real Kick recording responses composed through metadata and replay parsing."""

from __future__ import annotations

from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.replay_service import get_vod_chat
from tests.kick_helpers import (
    FakeKickSession,
    FakeResponse,
    assert_replay_contract,
    load_fixture,
)


def test_recorded_web_metadata_and_four_reverse_pages_preserve_replay_contract():
    fixture = load_fixture("recorded_replay_sam.json")
    primary = FakeKickSession(
        [
            FakeResponse(fixture["legacy_video_status"], {}),
            FakeResponse(200, fixture["channel"]),
            *(FakeResponse(200, page["response"]) for page in fixture["pages"]),
        ]
    )
    web = FakeKickSession([FakeResponse(200, fixture["web_video"])])
    client = KickApiClient(session=primary, web_session=web)
    video_id = fixture["web_video"]["data"]["id"]
    try:
        chat = get_vod_chat(
            "sam", video_id, ChatRequest(**fixture["request"]), api_client=client
        )
        messages = list(chat)
        assert_replay_contract(messages, fixture["expected"])
        assert chat.id == video_id
        assert chat.status == "completed"
        assert chat.start_time == 600
        assert chat.duration == 120
        assert primary.requested_urls[:2] == [
            f"https://kick.com/api/v1/video/{video_id}",
            "https://kick.com/api/v2/channels/sam",
        ]
        assert web.requested_urls == [
            f"https://web.kick.com/api/v1/channels/328683/videos/{video_id}"
        ]
        assert [call[1]["params"] for call in primary.calls[2:]] == [
            {"cursor": page["request_cursor"]} for page in fixture["pages"]
        ]
        assert all(
            url == "https://kick.com/api/v2/channels/328683/messages"
            for url in primary.requested_urls[2:]
        )
        diagnostics = chat.diagnostics
        assert diagnostics["pages"] == 4
        assert diagnostics["raw_records"] == 13
        assert diagnostics["selected_records"] == 11
        assert diagnostics["emitted_records"] == 11
        assert diagnostics["before_start"] == diagnostics["skipped_records"] == 2
        assert diagnostics["history_complete"] is True
        assert diagnostics["termination_reason"] == "completed"
        assert diagnostics["metadata_end_disagreement_seconds"] == 96
        reply = next(
            message
            for message in messages
            if message["message_id"] == "333b8c05-50c5-4d99-a1d6-fccdd5d7e5e4"
        )["in_reply_to"]
        assert reply["message_id"] == "a97fce8e-1634-494b-b1be-2c31d5152cc6"
        assert reply["message"] == "Earlier replay message"
        assert reply["timestamp"] == 1790789859000000
        assert reply["author"]["display_name"].startswith("ReplayUser")
        newest_emote = next(
            message
            for message in messages
            if message["message_id"] == "f3d41b40-57f4-49a7-b8b4-288fc3430405"
        )["emotes"]
        assert [(emote["id"], emote["name"]) for emote in newest_emote] == [
            ("3331556", "djrhysandthesoundletsgodj")
        ]
    finally:
        client.close()
    assert primary.close_calls == web.close_calls == 1

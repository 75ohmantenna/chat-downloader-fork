# SPDX-License-Identifier: MIT

"""Offline validation of the opt-in Kick realtime check's channel selection."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from chat_downloader.errors import UserNotFound
from tests import test_kick_network as live_check
from tests import test_kick_replay_network as replay_check
from tests.kick_helpers import load_fixture


@pytest.mark.parametrize("channel", [None, "", "../PRIVATE"])
def test_unusable_channel_never_creates_downloader(monkeypatch, channel):
    if channel is None:
        monkeypatch.delenv("KICK_TEST_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("KICK_TEST_CHANNEL", channel)
    create_downloader = Mock()
    monkeypatch.setattr(live_check, "KickChatDownloader", create_downloader)
    expected = pytest.fail.Exception if channel else pytest.skip.Exception
    with pytest.raises(expected, match="KICK_TEST_CHANNEL") as error:
        live_check.test_live_channel_receives_confirmed_realtime_delivery()
    create_downloader.assert_not_called()
    assert "PRIVATE" not in str(error.value)


def test_configured_nonexistent_channel_fails_realtime_check(monkeypatch):
    monkeypatch.setenv("KICK_TEST_CHANNEL", "chosen")
    downloader = Mock()
    monkeypatch.setattr(live_check, "KickChatDownloader", Mock(return_value=downloader))
    get_chat = Mock(side_effect=UserNotFound)
    monkeypatch.setattr(live_check, "get_chat_by_channel", get_chat)

    with pytest.raises(UserNotFound):
        live_check.test_live_channel_receives_confirmed_realtime_delivery()

    args = get_chat.call_args.args
    assert args[:2] == (downloader, "chosen")
    assert args[2].url == "https://kick.com/chosen"
    downloader.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"url": "https://kick.com/sam"}, "requires a Kick VOD URL"),
        ({"url": "https://not-kick.example/sam/videos/123"}, "requires a Kick VOD URL"),
        ({"recording_id": "wrong"}, "recording_id must match"),
        ({"start_time": "nan"}, "finite recording-relative bounds"),
        ({"start_time": "2026-11-20T12:00:00Z"}, "finite recording-relative bounds"),
        ({"end_time": 0}, "finite recording-relative bounds"),
        ({"expected": {}}, "requires independently known message IDs"),
        ({"expected": {"ID": {"timestamp": 0}}}, "times must be inside"),
        (
            {"expected": {"ID": {"timestamp": 0, "time_in_seconds": 735}}},
            "times must be inside",
        ),
        ({"text_probes": {}}, "requires text probes"),
        ({"text_probes": {"other": "ok"}}, "strings keyed by expected IDs"),
    ],
)
def test_invalid_replay_contract_never_creates_downloader(
    tmp_path,
    monkeypatch,
    updates,
    reason,
):
    case = load_fixture("replay_network_case_sam.json")
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case | updates), encoding="utf-8")
    monkeypatch.setenv("KICK_TEST_REPLAY_CASE", str(path))
    create_downloader = Mock()
    monkeypatch.setattr(replay_check, "ChatDownloader", create_downloader)

    with pytest.raises(pytest.fail.Exception, match=reason):
        replay_check.test_external_replay_matches_independently_recorded_message_contract()
    create_downloader.assert_not_called()


def test_replay_accepts_seconds_and_colon_separated_offsets():
    case = load_fixture("replay_network_case_sam.json")
    replay_check._validate_case(case | {"start_time": "11:54", "end_time": "734.0"})


@pytest.mark.parametrize("prefix", ["https://", "//", ""])
def test_replay_accepts_vod_urls_with_optional_scheme(prefix):
    case = load_fixture("replay_network_case_sam.json")
    url = prefix + case["url"].removeprefix("https://")
    replay_check._validate_case(case | {"url": url})

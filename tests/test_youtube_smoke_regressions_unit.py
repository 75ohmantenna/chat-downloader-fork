# SPDX-License-Identifier: MIT

"""Compose fixes for degraded bootstrap, mobile banners, and capture timing."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader import run
from chat_downloader.errors import LoginRequired, ParsingError
from chat_downloader.formatting import ItemFormatter
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.video_status import parse_video_details
from tests.test_youtube_initial_profile_fallback_unit import _Downloader
from tests.youtube_third_helpers import http_response, item_action, response, video_info

_FIXTURES = Path(__file__).parent / "fixtures/youtube"


def _degraded():
    return json.loads(
        (_FIXTURES / "innertube_bootstrap/degraded-live-watch.json").read_text()
    )


def _banner():
    payload = json.loads(
        (_FIXTURES / "live_events/youtube-mobile-summary-banner.json").read_text()
    )
    return payload["continuationContents"]["liveChatContinuation"]["actions"][0]


@pytest.mark.parametrize("replay", [None, False, True, "true", 0])
def test_degraded_bootstrap_uses_chat_evidence_and_retains_request_id(replay):
    fixture = _degraded()
    renderer = fixture["initial_data"]["contents"]["twoColumnWatchNextResults"][
        "conversationBar"
    ]["liveChatRenderer"]
    renderer["isReplay"] = replay
    details = parse_video_details(
        fixture["player_response"], fixture["initial_data"], "fixture-video"
    )
    assert details.status == (
        "post_live"
        if replay is True
        else "live"
        if replay is None or replay is False
        else "unknown"
    )
    assert details.original_video_id == "fixture-video"
    assert details.video_type == "video"
    assert details.start_time is None


def test_degraded_live_bootstrap_selects_live_page_and_poll(monkeypatch, tmp_path):
    fixture = _degraded()
    player = json.dumps(fixture["player_response"])
    data = json.dumps(fixture["initial_data"])
    initial_html = (
        f"var ytInitialData = {data};\n"
        'ytcfg.set({"INNERTUBE_API_KEY": "fixture"});\n'
        f"var ytInitialPlayerResponse = {player};\n"
    )
    header = {
        "liveChatHeaderRenderer": {
            "viewSelector": {
                "sortFilterSubMenuRenderer": {
                    "subMenuItems": [
                        {
                            "title": "Live chat",
                            "continuation": {
                                "reloadContinuationData": {
                                    "continuation": "fixture-live"
                                }
                            },
                        }
                    ]
                }
            }
        }
    }
    chat_html = (
        "var ytInitialData = "
        + json.dumps(
            {"continuationContents": {"liveChatContinuation": {"header": header}}}
        )
        + ";\n"
    )
    endpoints = []

    def get(_site, url, **kwargs):
        endpoints.append(url)
        return http_response(text=initial_html if "/watch?" in url else chat_html)

    def post(_site, url, **kwargs):
        endpoints.append(url)
        return http_response(
            payload=response(
                [
                    item_action(
                        "liveChatTextMessageRenderer",
                        {"id": "one", "message": {"simpleText": "hello"}},
                    )
                ]
            )
        )

    monkeypatch.setattr(YouTubeChatDownloader, "_session_get", get)
    monkeypatch.setattr(YouTubeChatDownloader, "_session_post", post)
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        max_messages=1,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        verify_output=True,
        quiet=True,
    )
    assert result.success
    assert result.parity_status == "passed"
    assert any("/live_chat?" in endpoint for endpoint in endpoints)
    assert any("/get_live_chat?" in endpoint for endpoint in endpoints)
    assert all("replay" not in endpoint for endpoint in endpoints)


@pytest.mark.parametrize("auto", [False, True])
def test_unknown_status_never_guesses_replay_and_fallback_is_bounded(auto):
    result = (
        {"status": "unknown", "continuation_info": {"Live chat": "token"}},
        {},
        {},
        {},
    )
    downloader = _Downloader([deepcopy(result) for _ in range(3)], auto_fallback=auto)
    with pytest.raises(ParsingError, match="did not establish live or replay status"):
        downloader._get_initial_video_info("abcdefghijk")
    assert downloader.parse_calls == (3 if auto else 1)


def test_unknown_status_recovers_with_new_profile_without_chat_page_guessing():
    unknown = ({"status": "unknown", "continuation_info": {}}, {}, {}, {})
    recovered = (
        {"status": "live", "continuation_info": {"Live chat": "fresh"}},
        {},
        {"_chat_downloader_continuation_info": True},
        {},
    )
    downloader = _Downloader([unknown, recovered])
    details, config = downloader._get_initial_video_info("abcdefghijk")
    assert details["continuation_info"] == {"Live chat": "fresh"}
    assert downloader.applied_profiles == ["youtube_android"]
    assert (
        config["_chat_downloader_bootstrap_diagnostics"][
            "bootstrap_profile_switch_count"
        ]
        == 1
    )


def test_missing_metadata_does_not_rotate_a_login_restriction():
    player = {
        "playabilityStatus": {
            "status": "LOGIN_REQUIRED",
            "errorScreen": {
                "playerErrorMessageRenderer": {
                    "reason": {"simpleText": "Please sign in"}
                }
            },
        }
    }
    downloader = _Downloader([({"status": "unknown"}, player, {}, {})])
    with pytest.raises(LoginRequired, match="Please sign in"):
        downloader._get_initial_video_info("abcdefghijk")
    assert downloader.applied_profiles == []


@pytest.mark.parametrize(
    "initial",
    [
        {},
        {
            "items": [
                {
                    "liveChatRenderer": {
                        "isReplay": False,
                        "continuations": [
                            None,
                            {},
                            {"reloadContinuationData": {"continuation": 1}},
                        ],
                    }
                }
            ]
        },
        {
            "items": [
                {"liveChatRenderer": {"isReplay": True}},
                {
                    "liveChatRenderer": {
                        "continuations": [
                            {"reloadContinuationData": {"continuation": "live"}}
                        ]
                    }
                },
            ]
        },
    ],
)
def test_missing_or_conflicting_chat_evidence_stays_unknown(initial):
    assert parse_video_details({}, initial, "fixture").status == "unknown"


def test_missing_clip_metadata_does_not_invent_an_original_video_id():
    details = parse_video_details({}, {}, "clip-id", video_type="clip")
    assert details.original_video_id is None


def test_renderer_only_replay_cannot_certify_archive_completion(monkeypatch, tmp_path):
    from dataclasses import asdict

    details = parse_video_details(
        {}, {"contents": {"liveChatRenderer": {"isReplay": True}}}, "abcdefghijk"
    )
    details.continuation_info = {"Live chat replay": "fixture"}
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_initial_video_info",
        lambda *_: (asdict(details), {"INNERTUBE_API_KEY": "fixture"}),
    )
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        require_complete=True,
        output=[str(tmp_path / "chat.jsonl")],
        quiet=True,
    )
    assert not result.success
    assert "completed replay" in result.error_message
    assert not (tmp_path / "chat.jsonl").exists()


@pytest.mark.parametrize("replay", [False, True])
def test_mobile_summary_capture_retains_content_timing_and_parity(
    monkeypatch, tmp_path, replay
):
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_initial_video_info",
        lambda *_: (
            video_info("was_live" if replay else "live"),
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    actions = [
        _banner(),
        item_action(
            "liveChatTextMessageRenderer",
            {"id": "one", "message": {"simpleText": "hello"}},
        ),
        item_action(
            "liveChatTextMessageRenderer",
            {
                "id": "source",
                "message": {"simpleText": "source"},
                "timestampUsec": "1600000000000000",
            },
        ),
    ]
    if replay:
        actions = [
            {
                "replayChatItemAction": {
                    "videoOffsetTimeMsec": "1000",
                    "actions": [action],
                }
            }
            for action in actions
        ]
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_post",
        lambda *_args, **_kwargs: http_response(payload=response(deepcopy(actions))),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.time_ns",
        lambda: 1_700_000_000_000_000_000,
    )
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        message_groups=["all"],
        max_messages=3,
        quiet=True,
        verify_output=True,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
    )
    assert result.success
    assert result.parity_status == "passed"
    assert result.provider_diagnostics.get("parse_error", 0) == 0
    records = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    summary = records[0]
    assert records[2]["timestamp"] == 1_600_000_000_000_000
    assert "received_timestamp" not in records[2]
    assert summary["message_type"] == "banner_chat_summary"
    assert summary["message"] == "Viewers discuss the match."
    assert summary["summary_id"] == "fixture-summary"
    assert "timestamp" not in summary
    if replay:
        assert summary["time_in_seconds"] == 1
        assert "received_timestamp" not in summary
        assert result.provider_inspection["timing_status"] == "missing"
    else:
        assert summary["received_timestamp"] == 1_700_000_000_000_000
        assert "time_in_seconds" not in summary
        assert result.provider_inspection["received_timestamp_fallbacks"] == 2
        assert result.provider_inspection["timing_status"] == "receive_fallback"
        assert "[received]" in (tmp_path / "chat.txt").read_text()


@pytest.mark.parametrize(
    "format_name",
    ["youtube_live_default", "youtube_live_24_hour", "youtube_live_12_hour"],
)
def test_youtube_receive_time_fallback_is_labeled_and_source_time_wins(format_name):
    formatter = ItemFormatter()
    message = {
        "message_type": "text_message",
        "received_timestamp": 1_700_000_000_000_000,
        "message": "hello",
    }
    assert "[received]" in formatter.format(message, format_name=format_name)
    message["timestamp"] = 1_600_000_000_000_000
    assert "[received]" not in formatter.format(message, format_name=format_name)


@pytest.mark.parametrize(
    "summary",
    [None, {}, {"chatSummary": {"elementsAttributedString": {"content": ""}}}],
)
def test_empty_mobile_summary_remains_a_parser_loss(summary):
    from chat_downloader.sites.filters import MessageFilter
    from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
    from chat_downloader.sites.youtube.message_pipeline import (
        NonEmissionReason,
        process_pipeline_action,
    )

    action = _banner()
    models = action["addBannerToLiveChatCommand"]["bannerRenderer"][
        "liveChatBannerRenderer"
    ]["contents"]["elementRenderer"]["newElement"]["type"]["componentType"]["model"]
    models["liveChatSummaryBannerModel"] = {
        "liveChatBannerChatSummaryRenderer": summary
    }
    result = process_pipeline_action(
        action, 0, MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"]), None
    )
    assert result.non_emission_reason is NonEmissionReason.INVALID_MESSAGE

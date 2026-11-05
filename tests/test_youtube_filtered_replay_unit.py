# SPDX-License-Identifier: MIT

"""Compose captured replay actions with filtering, cache, and continuation limits."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader.errors import InvalidParameter
from chat_downloader.models import ChatRequest
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action
from chat_downloader.sites.youtube.paid_events import PaidEventCache
from tests.youtube_third_helpers import http_response, response, video_info

_FIXTURES = Path(__file__).parent / "fixtures" / "youtube" / "live_events"


def _captured_actions():
    payload = json.loads((_FIXTURES / "replay-filter-end-boundary.json").read_text())
    return payload["continuationContents"]["liveChatContinuation"]["actions"]


@pytest.mark.parametrize("types", [["paid_message"], ["paid_sticker"], ["all"]])
def test_excluded_text_stops_real_replay_at_end(monkeypatch, types):
    actions = _captured_actions()
    page = response(
        actions,
        continuations=[
            {"timedContinuationData": {"continuation": "next", "timeoutMs": 5000}}
        ],
    )
    post = Mock(side_effect=[http_response(payload=page), AssertionError("overscan")])
    with closing(YouTubeChatDownloader()) as site:
        monkeypatch.setattr(site, "_session_post", post)
        monkeypatch.setattr(
            site,
            "_get_initial_video_info",
            lambda *_args: (video_info("was_live"), {"INNERTUBE_API_KEY": "fixture"}),
        )
        chat = site.get_chat_by_video_id(
            "9gFbjZhIZ2U", ChatRequest(start_time=0, end_time=60, message_types=types)
        )
        assert list(chat) == []
        assert post.call_count == 1
        assert chat.diagnostics["non_emission_counts"]["time-range stop"] == 1
        assert not chat.diagnostics.get("parse_error")


@pytest.mark.parametrize("types", [["all"], ["all", "future_type"], ["future_type"]])
def test_real_replay_type_validation_respects_wildcard(monkeypatch, types):
    page = response(_captured_actions())
    post = Mock(return_value=http_response(payload=page))
    with closing(YouTubeChatDownloader()) as site:
        monkeypatch.setattr(site, "_session_post", post)
        monkeypatch.setattr(
            site,
            "_get_initial_video_info",
            lambda *_args: (video_info("was_live"), {"INNERTUBE_API_KEY": "fixture"}),
        )
        chat = site.get_chat_by_video_id(
            "9gFbjZhIZ2U",
            ChatRequest(
                start_time=0,
                message_types=types,
                message_groups=["not-a-group"],
            ),
        )
        if types == ["future_type"]:
            with pytest.raises(
                InvalidParameter, match=r"Invalid message types.*Valid types:"
            ):
                list(chat)
            post.assert_not_called()
        else:
            messages = list(chat)
            assert [message["message_type"] for message in messages] == [
                "text_message",
                "chat_ended",
            ]
            assert [message["message_id"] for message in messages[:-1]] == [
                "fixture-1",
            ]
            assert messages[-1] == {
                "message_type": "chat_ended",
                "action_type": "chat_ended",
                "message": None,
            }
            assert post.call_count == 1


def test_type_exclusion_preserves_paid_cache_before_time_filter():
    payload = json.loads(
        (_FIXTURES / "youtube-replay-paid-ticker-shared-offset.json").read_text()
    )
    actions = payload["continuationContents"]["liveChatContinuation"]["actions"]
    ticker = actions[1]["replayChatItemAction"]
    ticker["videoOffsetTimeMsec"] = "1140000"
    ticker["actions"][0]["addLiveChatTickerItemAction"]["item"][
        "liveChatTickerPaidMessageItemRenderer"
    ].pop("showItemEndpoint")
    cache = PaidEventCache()
    filter_ = MessageFilter(_MESSAGE_GROUPS, types_to_add=["ticker_paid_message_item"])
    results = [
        process_pipeline_action(
            deepcopy(action),
            0,
            filter_,
            TimeRangeFilter(1138, 1200, skip_mode="always"),
            cache,
        )
        for action in actions
    ]
    tickers = [r.message for r in results if r.message]
    assert tickers
    assert tickers[0]["money"]["amount"] > 0


@pytest.mark.parametrize("end", [-1, 0, 61.284, 61.285])
def test_end_boundary_applies_even_to_excluded_type(end):
    action = _captured_actions()[1]
    result = process_pipeline_action(
        deepcopy(action),
        0,
        MessageFilter(_MESSAGE_GROUPS, types_to_add=["paid_message"]),
        TimeRangeFilter(0, end, skip_mode="always"),
    )
    assert result.disposition == ("stop" if end < 61.285 else "skip")

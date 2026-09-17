# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.youtube import video_initialization, video_metadata
from chat_downloader.sites.youtube.video_initialization import (
    YouTubeVideoInitializationMixin,
)
from chat_downloader.sites.youtube.video_metadata import YouTubeVideoMetadataCoreMixin
from chat_downloader.sites.youtube.video_status import parse_video_details
from tests.youtube_third_helpers import (
    http_response,
    returns,
    submenu_item,
    watch_chat,
    wrap,
)

URL = "https://www.youtube.com/watch?v=abc"


class _Metadata(YouTubeVideoMetadataCoreMixin):
    _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)
    _session_get = Mock(return_value=http_response(text=""))


class _Initialization(YouTubeVideoInitializationMixin):
    _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

    def __init__(self, result):
        self._parse_video_data = Mock(return_value=result)
        self._session_get = Mock(return_value=http_response(text="<html></html>"))


@pytest.mark.parametrize("clip", [False, True])
def test_video_data_selects_url_and_serializes_details(monkeypatch, clip):
    player = (
        {}
        if clip
        else {
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {"videoId": "abc", "title": "Example", "isLive": True},
        }
    )
    initial = {"contents": {"id": 1}} if clip else {"contents": {}}
    fetch = Mock(return_value=(initial, {"cfg": True}, player))
    monkeypatch.setattr(video_metadata, "_get_initial_info", fetch)
    logs = Mock()
    monkeypatch.setattr(video_metadata, "log", logs)
    details, response, data, config = _Metadata()._parse_video_data(
        "abc",
        None if clip else ChatRequest(url=URL),
        video_type="clip" if clip else "video",
    )
    route = "clip/abc" if clip else "watch?v=abc"
    assert fetch.call_args.args[0] == f"https://www.youtube.com/{route}"
    assert details["title"] == (None if clip else "Example")
    assert (response, data, config) == (player, initial, {"cfg": True})
    if clip:
        assert [c.args[0] for c in logs.call_args_list] == ["debug", "warning"]


@pytest.mark.parametrize(
    "params", [None, ChatRequest(url=URL), {"url": URL, "max_messages": 2}]
)
def test_video_data_normalizes_params(params):
    owner = _Metadata()
    owner._parse_video_data = Mock(return_value=({"id": "abc"}, {}, {}, {}))
    assert owner.get_video_data("abc", params) == {"id": "abc"}
    request = owner._parse_video_data.call_args.args[1]
    if isinstance(params, dict):
        assert isinstance(request, ChatRequest)
        assert request.url == params["url"]
        assert request.max_messages == 2
    else:
        assert request is params


@pytest.mark.parametrize(
    ("status", "items", "expected"),
    [
        (
            "live",
            [submenu_item("top-live"), submenu_item("live-chat")],
            {"Top chat": "top-live", "Live chat": "live-chat"},
        ),
        (
            "live",
            [
                submenu_item("live-chat", "continuationCommand", "Live chat"),
                submenu_item("top-live", "getLiveChatEndpoint", "Top chat"),
            ],
            {"Live chat": "live-chat", "Top chat": "top-live"},
        ),
        (
            "past",
            [submenu_item("top-replay"), submenu_item("live-replay")],
            {"Top chat replay": "top-replay", "Live chat replay": "live-replay"},
        ),
        (
            "past",
            [
                submenu_item("top-replay"),
                submenu_item("live-replay", "continuationCommand"),
            ],
            {"Top chat replay": "top-replay", "Live chat replay": "live-replay"},
        ),
    ],
)
def test_initial_info_enriches_chat_submenus(monkeypatch, status, items, expected):
    replay = status == "past"
    token = "client-replay-token" if replay else "client-live-token"
    config = {"cfg": True} if replay else {"INNERTUBE_CLIENT_NAME": "web"}
    owner = _Initialization(
        (
            {"status": status, "continuation_info": {}},
            {"playabilityStatus": {"status": "OK"}},
            watch_chat({"continuations": [submenu_item(token)["continuation"]]}),
            config,
        )
    )
    header = wrap(
        "liveChatHeaderRenderer.viewSelector.sortFilterSubMenuRenderer.subMenuItems",
        items,
    )
    bootstrap = wrap("continuationContents.liveChatContinuation.header", header)
    returns(monkeypatch, "video_initialization.regex_search", "{}")
    returns(monkeypatch, "video_initialization.try_parse_json", bootstrap)
    details, ytcfg = owner._get_initial_video_info("abc", None)
    route = "live_chat_replay" if replay else "live_chat"
    owner._session_get.assert_called_once_with(
        f"https://www.youtube.com/{route}?continuation={token}"
    )
    assert details["continuation_info"] == expected
    assert ytcfg == config


@pytest.mark.parametrize(
    ("continuations", "status", "initial", "warn"),
    [
        (
            {"Live chat": "token"},
            "OK",
            {"_chat_downloader_continuation_info": {"Live chat": "token"}},
            False,
        ),
        ({"Live chat": "existing"}, "ERROR", {"contents": {}}, False),
        ({}, "LOGIN_REQUIRED", {"contents": {}}, False),
        ({}, "LOGIN_REQUIRED", {"contents": {"unexpected": True}}, True),
    ],
)
def test_initial_info_without_bootstrap(
    monkeypatch, continuations, status, initial, warn
):
    details = {"status": "live", "continuation_info": continuations}
    owner = _Initialization(
        (details, {"playabilityStatus": {"status": status}}, initial, {"cfg": True})
    )
    returns(monkeypatch, "video_initialization.raise_if_playability_error", None)
    logs = Mock()
    monkeypatch.setattr(video_initialization, "log", logs)
    returned, config = owner._get_initial_video_info("abc", None)
    assert returned is details
    assert returned["continuation_info"] == continuations
    assert config == {"cfg": True}
    owner._session_get.assert_not_called()
    if warn:
        assert logs.call_args_list[0].args[0] == "warning"


@pytest.mark.parametrize("timestamps", [False, True])
def test_duration_falls_back_to_broadcast_timestamps(timestamps):
    response = {"videoDetails": {}, "microformat": {}}
    if timestamps:
        response["microformat"] = wrap(
            "playerMicroformatRenderer.liveBroadcastDetails",
            {
                "startTimestamp": "2024-01-01T00:00:00Z",
                "endTimestamp": "2024-01-01T01:00:00Z",
            },
        )
    else:
        response["liveStreamingDetails"] = {"scheduledStartTime": "1234567890"}
    assert parse_video_details(response, {}, "abc123").duration == (
        3600 if timestamps else None
    )

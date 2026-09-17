# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.youtube.video_initialization import (
    YouTubeVideoInitializationMixin,
)
from chat_downloader.sites.youtube.video_metadata import (
    YouTubeVideoMetadataCoreMixin,
)
from chat_downloader.sites.youtube.video_status import parse_video_details


class _MetadataDummy(YouTubeVideoMetadataCoreMixin):
    _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

    def __init__(self) -> None:
        self.session_calls: list[str] = []

    def _session_get(self, url: str):
        self.session_calls.append(url)
        return SimpleNamespace(text="")


class _InitializationDummy(YouTubeVideoInitializationMixin):
    _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

    def __init__(self, parse_result) -> None:
        self.parse_result = parse_result
        self.session_calls: list[str] = []

    def _parse_video_data(self, video_id, params, video_type="video"):
        return self.parse_result

    def _session_get(self, url: str):
        self.session_calls.append(url)
        return SimpleNamespace(text="<html></html>")


@pytest.mark.parametrize("video_type", ["video", "clip"])
def test_parse_video_data_selects_url_and_serializes_details(monkeypatch, video_type):
    from chat_downloader.sites.youtube import video_metadata

    dummy = _MetadataDummy()
    urls, logs = [], []
    clip = video_type == "clip"
    request = None if clip else ChatRequest(url="https://www.youtube.com/watch?v=abc")
    player_response = (
        {}
        if clip
        else {
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {"videoId": "abc", "title": "Example", "isLive": True},
        }
    )
    initial_data = {"contents": {"id": 1}} if clip else {"contents": {}}

    def initial_info(url, *_args):
        urls.append(url)
        return initial_data, {"cfg": True}, player_response

    monkeypatch.setattr(video_metadata, "_get_initial_info", initial_info)
    monkeypatch.setattr(
        video_metadata, "log", lambda level, value: logs.append((level, value))
    )
    details, response, initial, config = dummy._parse_video_data(
        "abc", request, video_type=video_type
    )

    route = "clip/abc" if clip else "watch?v=abc"
    assert urls == [f"https://www.youtube.com/{route}"]
    assert details["title"] == (None if clip else "Example")
    assert response == player_response
    assert initial == initial_data
    assert config == {"cfg": True}
    if clip:
        assert logs == [
            ("debug", initial_data),
            ("warning", "Unable to parse player response, proceeding with caution"),
        ]


@pytest.mark.parametrize(
    "params",
    [
        None,
        ChatRequest(url="https://www.youtube.com/watch?v=abc"),
        {"url": "https://www.youtube.com/watch?v=abc", "max_messages": 2},
    ],
)
def test_get_video_data_normalizes_params(params) -> None:
    class DummyVideoData(YouTubeVideoMetadataCoreMixin):
        def _parse_video_data(self, video_id, params=None, video_type="video"):
            self.request = params
            return ({"id": video_id}, {}, {}, {})

    dummy = DummyVideoData()
    assert dummy.get_video_data("abc", params) == {"id": "abc"}
    if isinstance(params, dict):
        assert isinstance(dummy.request, ChatRequest)
        assert dummy.request.url == params["url"]
        assert dummy.request.max_messages == 2
    else:
        assert dummy.request is params


def _watch_chat(renderer):
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "conversationBar": {"liveChatRenderer": renderer},
            },
        },
    }


def _submenu_item(token, endpoint="reloadContinuationData", title=None):
    if endpoint == "reloadContinuationData":
        item = {"continuation": {endpoint: {"continuation": token}}}
    else:
        key = "token" if endpoint == "continuationCommand" else "continuation"
        item = {"continuationEndpoint": {endpoint: {key: token}}}
    if title is not None:
        item["title"] = title
    return item


@pytest.mark.parametrize(
    ("status", "items", "expected"),
    [
        pytest.param(
            "live",
            [_submenu_item("top-live"), _submenu_item("live-chat")],
            {"Top chat": "top-live", "Live chat": "live-chat"},
            id="unlabeled-live",
        ),
        pytest.param(
            "live",
            [
                _submenu_item("live-chat", "continuationCommand", "Live chat"),
                _submenu_item("top-live", "getLiveChatEndpoint", "Top chat"),
            ],
            {"Live chat": "live-chat", "Top chat": "top-live"},
            id="reordered-labeled-endpoints",
        ),
        pytest.param(
            "past",
            [_submenu_item("top-replay"), _submenu_item("live-replay")],
            {"Top chat replay": "top-replay", "Live chat replay": "live-replay"},
            id="unlabeled-replay",
        ),
        pytest.param(
            "past",
            [
                _submenu_item("top-replay"),
                _submenu_item("live-replay", "continuationCommand"),
            ],
            {"Top chat replay": "top-replay", "Live chat replay": "live-replay"},
            id="unlabeled-mixed-replay-endpoints",
        ),
    ],
)
def test_initial_video_info_enriches_chat_submenus(
    monkeypatch,
    status,
    items,
    expected,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    replay = status == "past"
    token = "client-replay-token" if replay else "client-live-token"
    config = {"cfg": True} if replay else {"INNERTUBE_CLIENT_NAME": "web"}
    dummy = _InitializationDummy(
        (
            {"status": status, "continuation_info": {}},
            {"playabilityStatus": {"status": "OK"}},
            _watch_chat({"continuations": [_submenu_item(token)["continuation"]]}),
            config,
        ),
    )
    selector = {"sortFilterSubMenuRenderer": {"subMenuItems": items}}
    header = {"liveChatHeaderRenderer": {"viewSelector": selector}}
    bootstrap = {"continuationContents": {"liveChatContinuation": {"header": header}}}
    monkeypatch.setattr(video_initialization, "regex_search", lambda *_args: "{}")
    monkeypatch.setattr(
        video_initialization, "try_parse_json", lambda _value: bootstrap
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    route = "live_chat_replay" if replay else "live_chat"
    assert dummy.session_calls == [
        f"https://www.youtube.com/{route}?continuation={token}",
    ]
    assert returned_details["continuation_info"] == expected
    assert ytcfg == config


@pytest.mark.parametrize(
    ("continuations", "player_status", "initial_data", "warn"),
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
    ids=["existing-bootstrap", "existing-error", "missing-bootstrap", "invalid-shape"],
)
def test_initial_video_info_without_bootstrap(
    monkeypatch,
    continuations,
    player_status,
    initial_data,
    warn,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": continuations}
    player_response = {"playabilityStatus": {"status": player_status}}
    dummy = _InitializationDummy(
        (details, player_response, initial_data, {"cfg": True}),
    )
    warning_logs = []
    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        video_initialization,
        "log",
        lambda level, message: warning_logs.append((level, message)),
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    assert returned_details is details
    assert returned_details["continuation_info"] == continuations
    assert ytcfg == {"cfg": True}
    assert dummy.session_calls == []
    if warn:
        assert warning_logs
        assert warning_logs[0][0] == "warning"
        assert "Unable to enrich chat submenu continuation tokens" in warning_logs[0][1]


@pytest.mark.parametrize("timestamps", [False, True])
def test_parse_video_details_livestream_and_timestamp_duration(timestamps) -> None:
    # Missing format duration falls back to the broadcast timestamps.
    broadcast = {
        "startTimestamp": "2024-01-01T00:00:00Z",
        "endTimestamp": "2024-01-01T01:00:00Z",
    }
    response = {"videoDetails": {}, "microformat": {}}
    if timestamps:
        response["microformat"] = {
            "playerMicroformatRenderer": {"liveBroadcastDetails": broadcast}
        }
    else:
        response["liveStreamingDetails"] = {"scheduledStartTime": "1234567890"}
    result = parse_video_details(response, {}, "abc123")
    assert result.duration == (3600 if timestamps else None)

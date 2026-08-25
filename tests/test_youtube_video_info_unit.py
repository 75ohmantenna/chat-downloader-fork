# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from chat_downloader.models import ChatRequest
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.youtube.video_initialization import (
    YouTubeVideoInitializationMixin,
)
from chat_downloader.sites.youtube.video_metadata import (
    YouTubeVideoMetadataCoreMixin,
)
from chat_downloader.sites.youtube.video_status_models import VideoDetails


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


def test_parse_video_data_uses_watch_url_and_serializes_video_details(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_metadata

    dummy = _MetadataDummy()
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")
    calls = {}
    parsed_details = VideoDetails(
        title="Example",
        author="Uploader",
        author_id="channel-1",
        original_video_id="abc",
        video_type="video",
        status="live",
        start_time=1.0,
        end_time=2.0,
        duration=3.0,
        continuation_info={"Live chat": "token"},
    )

    def fake_get_initial_info(
        original_url,
        session_get,
        params,
        initial_data_re,
        cfg_re,
        initial_player_re,
    ):
        calls["original_url"] = original_url
        calls["session_get"] = session_get
        calls["params"] = params
        calls["regexes"] = (initial_data_re, cfg_re, initial_player_re)
        return (
            {"contents": {}},
            {"cfg": True},
            {"playabilityStatus": {"status": "OK"}},
        )

    def fake_parse_video_details(
        player_response_info,
        yt_initial_data,
        video_id,
        video_type,
    ):
        calls["parse_args"] = (
            player_response_info,
            yt_initial_data,
            video_id,
            video_type,
        )
        return parsed_details

    monkeypatch.setattr(video_metadata, "_get_initial_info", fake_get_initial_info)
    monkeypatch.setattr(video_metadata, "parse_video_details", fake_parse_video_details)
    monkeypatch.setattr(
        video_metadata,
        "video_details_to_dict",
        lambda details: {"title": details.title, "status": details.status},
    )

    details, player_response_info, yt_initial_data, ytcfg = dummy._parse_video_data(
        "abc",
        request,
    )

    assert calls["original_url"].endswith("/watch?v=abc")
    assert calls["session_get"] == dummy._session_get
    assert calls["params"] is request
    assert calls["parse_args"] == (
        {"playabilityStatus": {"status": "OK"}},
        {"contents": {}},
        "abc",
        "video",
    )
    assert details == {"title": "Example", "status": "live"}
    assert player_response_info == {"playabilityStatus": {"status": "OK"}}
    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {"cfg": True}


def test_parse_video_data_uses_clip_url_and_logs_missing_player_response(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_metadata

    dummy = _MetadataDummy()
    logs = []
    captured: dict[str, Any] = {}

    def fake_get_initial_info(original_url, *_args):
        captured["original_url"] = original_url
        return {"contents": {"id": 1}}, {"cfg": True}, {}

    monkeypatch.setattr(
        video_metadata,
        "_get_initial_info",
        fake_get_initial_info,
    )
    monkeypatch.setattr(
        video_metadata,
        "parse_video_details",
        lambda *_args: VideoDetails(
            title="Clip title",
            author=None,
            author_id=None,
            original_video_id="clip123",
            video_type="clip",
            status="past",
            start_time=None,
            end_time=None,
            duration=None,
        ),
    )
    monkeypatch.setattr(
        video_metadata,
        "video_details_to_dict",
        lambda details: {"title": details.title},
    )
    monkeypatch.setattr(
        video_metadata,
        "log",
        lambda level, value: logs.append((level, value)),
    )

    details, player_response_info, yt_initial_data, ytcfg = dummy._parse_video_data(
        "clip123",
        video_type="clip",
    )

    assert captured["original_url"].endswith("/clip/clip123")
    assert logs == [
        ("debug", {"contents": {"id": 1}}),
        ("warning", "Unable to parse player response, proceeding with caution"),
    ]
    assert details == {"title": "Clip title"}
    assert player_response_info == {}
    assert yt_initial_data == {"contents": {"id": 1}}
    assert ytcfg == {"cfg": True}


def test_get_video_data_returns_details_from_parse_result() -> None:
    class DummyVideoData(YouTubeVideoMetadataCoreMixin):
        def _parse_video_data(self, video_id, params=None, video_type="video"):
            return (
                {"id": video_id, "params": params, "type": video_type},
                {},
                {},
                {},
            )

    dummy = DummyVideoData()
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")

    assert dummy.get_video_data("abc", request) == {
        "id": "abc",
        "params": request,
        "type": "video",
    }


def test_get_video_data_handles_none_and_dict_params() -> None:
    class DummyVideoData(YouTubeVideoMetadataCoreMixin):
        def __init__(self) -> None:
            self.calls = []

        def _parse_video_data(self, video_id, params=None, video_type="video"):
            self.calls.append((video_id, params, video_type))
            return ({"id": video_id}, {}, {}, {})

    dummy = DummyVideoData()

    assert dummy.get_video_data("abc", None) == {"id": "abc"}
    assert dummy.calls[0] == ("abc", None, "video")

    assert dummy.get_video_data(
        "def",
        {"url": "https://www.youtube.com/watch?v=def", "max_messages": 2},
    ) == {"id": "def"}
    assert isinstance(dummy.calls[1][1], ChatRequest)
    assert dummy.calls[1][1].url == "https://www.youtube.com/watch?v=def"
    assert dummy.calls[1][1].max_messages == 2


def test_initial_video_info_adds_live_chat_continuations(monkeypatch) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {}}
    dummy = _InitializationDummy(
        (
            details,
            {"playabilityStatus": {"status": "OK"}},
            {
                "contents": {
                    "twoColumnWatchNextResults": {
                        "conversationBar": {
                            "liveChatRenderer": {
                                "continuations": [
                                    {
                                        "reloadContinuationData": {
                                            "continuation": "client-live-token",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            {"INNERTUBE_CLIENT_NAME": "web"},
        ),
    )
    raise_calls = []

    monkeypatch.setattr(video_initialization, "regex_search", lambda *_args: "{}")
    monkeypatch.setattr(
        video_initialization,
        "try_parse_json",
        lambda _value: {
            "continuationContents": {
                "liveChatContinuation": {
                    "header": {
                        "liveChatHeaderRenderer": {
                            "viewSelector": {
                                "sortFilterSubMenuRenderer": {
                                    "subMenuItems": [
                                        {
                                            "continuation": {
                                                "reloadContinuationData": {
                                                    "continuation": "top-live",
                                                },
                                            },
                                        },
                                        {
                                            "continuation": {
                                                "reloadContinuationData": {
                                                    "continuation": "live-chat",
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *args: raise_calls.append(args),
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    assert dummy.session_calls == [
        "https://www.youtube.com/live_chat?continuation=client-live-token",
    ]
    assert returned_details["continuation_info"] == {
        "Top chat": "top-live",
        "Live chat": "live-chat",
    }
    assert ytcfg == {"INNERTUBE_CLIENT_NAME": "web"}
    assert raise_calls == []


def test_initial_video_info_skips_bootstrap_when_continuations_exist(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {"Live chat": "token"}}
    player_response = {"playabilityStatus": {"status": "OK"}}
    yt_initial_data = {"_chat_downloader_continuation_info": {"Live chat": "token"}}
    dummy = _InitializationDummy(
        (details, player_response, yt_initial_data, {"cfg": True}),
    )
    raise_calls = []

    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *args: raise_calls.append(args),
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    assert returned_details is details
    assert ytcfg == {"cfg": True}
    assert dummy.session_calls == []
    assert raise_calls == []


def test_initial_video_info_maps_reordered_labeled_chat_submenus(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {}}
    dummy = _InitializationDummy(
        (
            details,
            {"playabilityStatus": {"status": "OK"}},
            {
                "contents": {
                    "twoColumnWatchNextResults": {
                        "conversationBar": {
                            "liveChatRenderer": {
                                "continuations": [
                                    {
                                        "reloadContinuationData": {
                                            "continuation": "client-live-token",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            {"INNERTUBE_CLIENT_NAME": "web"},
        ),
    )

    monkeypatch.setattr(video_initialization, "regex_search", lambda *_args: "{}")
    monkeypatch.setattr(
        video_initialization,
        "try_parse_json",
        lambda _value: {
            "continuationContents": {
                "liveChatContinuation": {
                    "header": {
                        "liveChatHeaderRenderer": {
                            "viewSelector": {
                                "sortFilterSubMenuRenderer": {
                                    "subMenuItems": [
                                        {
                                            "title": "Live chat",
                                            "continuationEndpoint": {
                                                "continuationCommand": {
                                                    "token": "live-chat",
                                                },
                                            },
                                        },
                                        {
                                            "title": "Top chat",
                                            "continuationEndpoint": {
                                                "getLiveChatEndpoint": {
                                                    "continuation": "top-live",
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    returned_details, _ytcfg = dummy._get_initial_video_info("abc", None)

    assert returned_details["continuation_info"] == {
        "Live chat": "live-chat",
        "Top chat": "top-live",
    }


def test_initial_video_info_adds_replay_chat_continuations(monkeypatch) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "past", "continuation_info": {}}
    dummy = _InitializationDummy(
        (
            details,
            {"playabilityStatus": {"status": "OK"}},
            {
                "contents": {
                    "twoColumnWatchNextResults": {
                        "conversationBar": {
                            "liveChatRenderer": {
                                "continuations": [
                                    {
                                        "reloadContinuationData": {
                                            "continuation": "client-replay-token",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            {"cfg": True},
        ),
    )

    monkeypatch.setattr(video_initialization, "regex_search", lambda *_args: "{}")
    monkeypatch.setattr(
        video_initialization,
        "try_parse_json",
        lambda _value: {
            "continuationContents": {
                "liveChatContinuation": {
                    "header": {
                        "liveChatHeaderRenderer": {
                            "viewSelector": {
                                "sortFilterSubMenuRenderer": {
                                    "subMenuItems": [
                                        {
                                            "continuation": {
                                                "reloadContinuationData": {
                                                    "continuation": "top-replay",
                                                },
                                            },
                                        },
                                        {
                                            "continuation": {
                                                "reloadContinuationData": {
                                                    "continuation": "live-replay",
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    assert dummy.session_calls == [
        "https://www.youtube.com/live_chat_replay?continuation=client-replay-token",
    ]
    assert returned_details["continuation_info"] == {
        "Top chat replay": "top-replay",
        "Live chat replay": "live-replay",
    }
    assert ytcfg == {"cfg": True}


def test_initial_video_info_uses_fallback_labels_for_unlabeled_replay_submenus(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "past", "continuation_info": {}}
    dummy = _InitializationDummy(
        (
            details,
            {"playabilityStatus": {"status": "OK"}},
            {
                "contents": {
                    "twoColumnWatchNextResults": {
                        "conversationBar": {
                            "liveChatRenderer": {
                                "continuations": [
                                    {
                                        "reloadContinuationData": {
                                            "continuation": "client-replay-token",
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            {"cfg": True},
        ),
    )

    monkeypatch.setattr(video_initialization, "regex_search", lambda *_args: "{}")
    monkeypatch.setattr(
        video_initialization,
        "try_parse_json",
        lambda _value: {
            "continuationContents": {
                "liveChatContinuation": {
                    "header": {
                        "liveChatHeaderRenderer": {
                            "viewSelector": {
                                "sortFilterSubMenuRenderer": {
                                    "subMenuItems": [
                                        {
                                            "continuation": {
                                                "reloadContinuationData": {
                                                    "continuation": "top-replay",
                                                },
                                            },
                                        },
                                        {
                                            "continuationEndpoint": {
                                                "continuationCommand": {
                                                    "token": "live-replay",
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    returned_details, _ytcfg = dummy._get_initial_video_info("abc", None)

    assert returned_details["continuation_info"] == {
        "Top chat replay": "top-replay",
        "Live chat replay": "live-replay",
    }


def test_initial_video_info_falls_back_to_playability_when_bootstrap_missing(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {}}
    player_response = {"playabilityStatus": {"status": "LOGIN_REQUIRED"}}
    yt_initial_data: dict[str, Any] = {"contents": {}}
    dummy = _InitializationDummy(
        (details, player_response, yt_initial_data, {"cfg": True}),
    )
    raise_calls = []

    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *args: raise_calls.append(args),
    )

    returned_details, ytcfg = dummy._get_initial_video_info("abc", None)

    assert dummy.session_calls == []
    assert returned_details is details
    assert ytcfg == {"cfg": True}
    assert raise_calls == [(player_response, yt_initial_data)]


def test_initial_video_info_logs_warning_when_bootstrap_shape_is_invalid(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {}}
    player_response = {"playabilityStatus": {"status": "LOGIN_REQUIRED"}}
    yt_initial_data: dict[str, Any] = {"contents": {"unexpected": True}}
    dummy = _InitializationDummy(
        (details, player_response, yt_initial_data, {"cfg": True}),
    )
    raise_calls = []
    warning_logs = []

    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *args: raise_calls.append(args),
    )
    monkeypatch.setattr(
        video_initialization,
        "log",
        lambda level, message: warning_logs.append((level, message)),
    )

    returned_details, _ = dummy._get_initial_video_info("abc", None)

    assert returned_details is details
    assert warning_logs
    assert warning_logs[0][0] == "warning"
    assert "Unable to enrich chat submenu continuation tokens" in warning_logs[0][1]
    assert raise_calls == [(player_response, yt_initial_data)]


def test_initial_video_info_skips_playability_check_when_continuation_exists(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import video_initialization

    details = {"status": "live", "continuation_info": {"Live chat": "existing"}}
    dummy = _InitializationDummy(
        (
            details,
            {"playabilityStatus": {"status": "ERROR"}},
            {"contents": {}},
            {"cfg": True},
        ),
    )

    monkeypatch.setattr(
        video_initialization,
        "raise_if_playability_error",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    returned_details, _ = dummy._get_initial_video_info("abc", None)

    assert returned_details["continuation_info"] == {"Live chat": "existing"}
    assert dummy.session_calls == []


def test_parse_video_details_triggers_livestreaming_debug_log() -> None:
    from chat_downloader.sites.youtube.video_status import (
        parse_video_details,
    )

    player_response_info = {
        "liveStreamingDetails": {"scheduledStartTime": "1234567890"},
        "videoDetails": {},
        "microformat": {},
    }
    result = parse_video_details(player_response_info, {}, "abc123")
    assert result is not None


def test_parse_video_details_duration_from_start_end_timestamps() -> None:
    from chat_downloader.sites.youtube.video_status import (
        parse_video_details,
    )

    # No approxDurationMs, no lengthSeconds → duration falls back to timestamps
    player_response_info = {
        "videoDetails": {},
        "microformat": {
            "playerMicroformatRenderer": {
                "liveBroadcastDetails": {
                    "startTimestamp": "2024-01-01T00:00:00Z",
                    "endTimestamp": "2024-01-01T01:00:00Z",
                }
            }
        },
    }
    result = parse_video_details(player_response_info, {}, "abc123")
    assert result.duration is not None
    assert result.duration > 0

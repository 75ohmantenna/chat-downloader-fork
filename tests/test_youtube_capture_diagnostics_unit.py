# SPDX-License-Identifier: MIT

"""Bootstrap diagnostics belong to the capture that performs the request."""

from __future__ import annotations

from contextlib import closing
from threading import Event
from unittest.mock import Mock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from chat_downloader import ChatDownloader
from chat_downloader.errors import VideoNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.runner import RunResult, execute_run
from chat_downloader.sites.youtube.bootstrap_diagnostics import BootstrapRequests
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response, response

_VIDEO_ID = "sxLJv_jA6sE"
_VIDEO_URL = f"https://www.youtube.com/watch?v={_VIDEO_ID}"
_CHANNEL_URL = "https://www.youtube.com/channel/UC0000000000000000000000"


def _discover(**_kwargs):
    yield {"video_id": _VIDEO_ID, "title": "fixture", "video_type": "LIVE"}


def test_facade_retains_channel_and_early_failure_bootstrap_counters(monkeypatch):
    monkeypatch.setattr(
        YouTubeChatDownloader, "get_user_videos", Mock(side_effect=_discover)
    )
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_get",
        Mock(side_effect=RequestsConnectionError("offline")),
    )

    with (
        closing(ChatDownloader()) as downloader,
        downloader.get_chat(_CHANNEL_URL, max_attempts=1) as channel,
    ):
        setup = downloader._request_diagnostics
        with pytest.raises(RequestsConnectionError):
            downloader.get_chat(_VIDEO_URL, max_attempts=2, retry_timeout=0)
        with pytest.raises(RequestsConnectionError):
            next(channel)

        assert setup["bootstrap_request_count"] == 1
        assert setup["bootstrap_network_error_count"] == 1
        assert downloader._request_diagnostics["bootstrap_request_count"] == 2

        result = RunResult()
        result.observe_provider(channel, downloader)
        assert result.provider_diagnostics["bootstrap_request_count"] == 1


def _watch_page(_url, get, _params, *_patterns):
    get(_url)
    return (
        {"_chat_downloader_continuation_info": {"Live chat": "first"}},
        {"INNERTUBE_API_KEY": "fixture"},
        {
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {
                "videoId": _VIDEO_ID,
                "title": "fixture",
                "isLive": True,
            },
            "clipConfig": {"startTimeMs": "2000", "endTimeMs": "7000"},
        },
    )


@pytest.mark.parametrize("target", ["video", "clip", "channel"])
def test_direct_captures_reuse_request_without_sharing_bootstrap_counters(
    monkeypatch, target
):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata._get_initial_info", _watch_page
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        Mock(return_value={"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
    )
    request = ChatRequest(max_attempts=1, end_time=4)

    with closing(YouTubeChatDownloader(request_profile="youtube_web")) as site:
        monkeypatch.setattr(site, "get_user_videos", _discover)
        monkeypatch.setattr(site, "_session_get", Mock(return_value=http_response()))
        method = {
            "video": site.get_chat_by_video_id,
            "clip": site.get_chat_by_clip_id,
            "channel": site.get_chat_by_channel_id,
        }[target]
        with (
            method(_VIDEO_ID, request) as first,
            site.get_chat_by_video_id(_VIDEO_ID, request) as second,
        ):
            assert second.diagnostics["bootstrap_request_count"] == 1
            assert request.end_time == 4
            if target == "channel":
                # A network error escapes the channel's normal candidate fallback.
                monkeypatch.setattr(
                    site, "_session_get", Mock(side_effect=RequestsConnectionError)
                )
                failure, expected = RequestsConnectionError, 1
            else:
                # A rejected poll switches profiles and refreshes the watch setup.
                monkeypatch.setattr(
                    site,
                    "_session_post",
                    Mock(return_value=http_response(status_code=404)),
                )
                failure, expected = VideoNotFound, 2
            with pytest.raises(failure):
                next(first)

            result = RunResult()
            result.observe_provider(first, site)
            assert result.provider_diagnostics["bootstrap_request_count"] == expected
            result.observe_provider(second, site)
            assert result.provider_diagnostics["bootstrap_request_count"] == 1
            assert first.diagnostics["bootstrap_request_count"] == expected
            assert first.diagnostics["initial_request_profile"] == "youtube_web"
            assert first.diagnostics["active_request_profile"] == (
                "youtube_web" if target == "channel" else "youtube_android"
            )


def test_reentrant_direct_capture_with_same_provider_and_request_is_independent(
    monkeypatch,
):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata._get_initial_info", _watch_page
    )
    request = ChatRequest(max_attempts=1)
    nested = []
    with closing(YouTubeChatDownloader()) as site:

        def get(_url):
            if fetch.call_count == 1:
                nested.append(site.get_chat_by_video_id(_VIDEO_ID, request))
            return http_response()

        fetch = Mock(side_effect=get)
        monkeypatch.setattr(site, "_session_get", fetch)
        with (
            site.get_chat_by_video_id(_VIDEO_ID, request) as first,
            nested[0] as second,
        ):
            for chat in (first, second):
                result = RunResult()
                result.observe_provider(chat, site)
                assert result.provider_diagnostics["bootstrap_request_count"] == 1


def _mobile_page(_video_id, post, _profile):
    post("fixture")
    return _watch_page("fixture", lambda _: None, None)


def test_successful_continuation_rebootstrap_counts_once(monkeypatch):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata._get_initial_info", _watch_page
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata.get_innertube_video_bootstrap",
        _mobile_page,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        Mock(
            side_effect=[
                {"error": {"code": 400, "status": "INVALID_ARGUMENT"}},
                response(continuations=[]),
            ]
        ),
    )

    with closing(YouTubeChatDownloader(request_profile="youtube_web")) as site:
        monkeypatch.setattr(site, "_session_get", Mock(return_value=http_response()))
        monkeypatch.setattr(site, "_session_post", Mock(return_value=http_response()))
        request = ChatRequest(max_attempts=1, message_groups=["all"])
        with site.get_chat_by_video_id(_VIDEO_ID, request) as chat:
            assert chat.diagnostics["bootstrap_request_count"] == 1
            assert next(chat)["message_type"] == "chat_ended"
            assert chat.diagnostics["bootstrap_request_count"] == 2
            assert chat.diagnostics["initial_request_profile"] == "youtube_web"
            assert chat.diagnostics["active_request_profile"] == "youtube_android"


def test_channel_counts_failed_and_successful_candidates_once(monkeypatch):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata._get_initial_info", _watch_page
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata.get_innertube_video_bootstrap",
        _mobile_page,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        Mock(return_value=response(continuations=[])),
    )

    def candidates(**_kwargs):
        yield {"video_id": "failed", "title": "failed", "video_type": "LIVE"}
        yield from _discover()

    with closing(YouTubeChatDownloader(request_profile="youtube_web")) as site:

        def rejected_watch(_url):
            site.apply_request_profile("youtube_android")
            raise VideoNotFound("offline candidate")

        monkeypatch.setattr(site, "get_user_videos", candidates)
        monkeypatch.setattr(site, "_session_get", rejected_watch)
        monkeypatch.setattr(site, "_session_post", Mock(return_value=http_response()))
        request = ChatRequest(max_attempts=1, message_groups=["all"])
        with site.get_chat_by_channel_id("fixture", request) as chat:
            assert next(chat)["message_type"] == "chat_ended"
            assert chat.diagnostics["bootstrap_request_count"] == 2
            assert chat.diagnostics["initial_request_profile"] == "youtube_web"
            assert chat.diagnostics["active_request_profile"] == "youtube_android"


@pytest.mark.parametrize(("url", "expected"), [(_CHANNEL_URL, 1), (_VIDEO_URL, 2)])
def test_timed_out_run_counts_started_bootstrap_and_freezes_before_late_failure(
    monkeypatch, url, expected
):
    started = Event()
    release = Event()
    recorded = Event()
    chats = []
    increment = BootstrapRequests.increment

    def block(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        raise RequestsConnectionError("offline after timeout")

    def record(monitor, key):
        increment(monitor, key)
        if key == "bootstrap_network_error_count":
            recorded.set()

    class Downloader(ChatDownloader):
        def get_chat(self, *args, **kwargs):
            chat = super().get_chat(*args, **kwargs)
            chats.append(chat)
            assert started.wait(timeout=5)
            chat.chat._timeout_deadline = 0
            return chat

    monkeypatch.setattr(BootstrapRequests, "increment", record)
    monkeypatch.setattr(
        YouTubeChatDownloader, "get_user_videos", Mock(side_effect=_discover)
    )
    if url == _CHANNEL_URL:
        monkeypatch.setattr(
            YouTubeChatDownloader, "_session_get", Mock(side_effect=block)
        )
    else:
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.video_metadata._get_initial_info",
            _watch_page,
        )
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.continuation._get_continuation_info",
            Mock(return_value={"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
        )
        monkeypatch.setattr(
            YouTubeChatDownloader, "_session_get", Mock(return_value=http_response())
        )
        monkeypatch.setattr(
            YouTubeChatDownloader, "_session_post", Mock(side_effect=block)
        )

    try:
        result = execute_run(
            Downloader,
            url=url,
            timeout=60,
            max_attempts=1,
            quiet=True,
            request_profile="youtube_web",
        )
        assert result.termination_reason == "timeout"
        assert result.provider_diagnostics["bootstrap_request_count"] == expected
        assert result.provider_diagnostics["bootstrap_network_error_count"] == 0
    finally:
        release.set()

    assert recorded.wait(timeout=5)
    assert chats[0].diagnostics["bootstrap_network_error_count"] == 1
    assert result.provider_diagnostics["bootstrap_network_error_count"] == 0

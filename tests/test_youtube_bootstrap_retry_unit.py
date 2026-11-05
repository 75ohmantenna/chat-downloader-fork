# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from requests.exceptions import HTTPError, Timeout

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    NoChatReplay,
    ParsingError,
    VideoNotFound,
)
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.video_initialization import (
    YouTubeVideoInitializationMixin,
)
from chat_downloader.sites.youtube.video_metadata import YouTubeVideoMetadataCoreMixin
from chat_downloader.utils import retry_utils
from chat_downloader.utils.timed_generator import _POLLING_STOP_EVENT, _PollingCancelled
from tests.youtube_third_helpers import http_response, submenu_item, watch_chat

_FIXTURES = Path(__file__).parent / "fixtures/youtube/innertube_bootstrap"
_VIDEO = "IzopCEgh2G8"


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _failures():
    return _fixture("retry-responses-synthetic.json")


class _FinalFallback(YouTubeVideoMetadataCoreMixin):
    _request_profile = "youtube_web"

    def __init__(self, endpoint, responses):
        self.endpoint = endpoint
        self.responses = iter(responses)
        self.posts = []
        self._request_diagnostics = {}

    def _session_get(self, _url, **_kwargs):
        raise ParsingError("watch bootstrap unavailable")

    def _session_post(self, url, **kwargs):
        endpoint = "player" if "/player?" in url else "next"
        self.posts.append((endpoint, kwargs["json"]))
        if endpoint == self.endpoint:
            result = next(self.responses)
            if isinstance(result, BaseException):
                raise result
            if result is not None:
                return result
        return http_response(payload=_fixture(f"youtube-{_VIDEO}-{endpoint}-web.json"))


def _request():
    return ChatRequest(max_attempts=2, retry_timeout=0)


@pytest.mark.parametrize("endpoint", ["player", "next"])
@pytest.mark.parametrize(
    "failure", ["503", "timeout", "json", "null", "incomplete", "api"]
)
def test_final_fallback_retries_only_the_failed_operation(endpoint, failure):
    fixtures = _failures()
    bad = {
        "503": http_response(status_code=503),
        "timeout": Timeout("offline timeout"),
        "json": http_response(),
        "null": http_response(payload=None),
        "incomplete": http_response(payload=fixtures[f"incomplete_{endpoint}"]),
        "api": http_response(payload=fixtures["transient_error"]),
    }[failure]
    if failure == "json":
        bad.json.side_effect = ValueError("invalid JSON")
    site = _FinalFallback(endpoint, [bad, None])
    details = site.get_video_data(_VIDEO, _request())
    assert details["status"] == "live"
    assert details["continuation_info"]["Live chat"].startswith("0ofMyAO")
    assert [name for name, _ in site.posts] == (
        ["player", "player", "next"]
        if endpoint == "player"
        else ["player", "next", "next"]
    )
    assert all(p["context"]["client"]["clientName"] == "WEB" for _, p in site.posts)
    counters = site._request_diagnostics
    assert counters["bootstrap_request_count"] == 4
    assert counters["bootstrap_fallback_count"] == 1
    assert counters["bootstrap_profile_switch_count"] == 0
    assert counters["bootstrap_http_error_count"] == (1 if failure == "503" else 0)
    assert counters["bootstrap_network_error_count"] == (
        1 if failure == "timeout" else 0
    )


@pytest.mark.parametrize("endpoint", ["player", "next"])
@pytest.mark.parametrize("failure", ["503", "timeout", "incomplete"])
def test_final_fallback_exhaustion_is_bounded(endpoint, failure):
    error, expected = {
        "503": (http_response(status_code=503), HTTPError),
        "timeout": (Timeout("offline"), Timeout),
        "incomplete": (http_response(payload={}), ParsingError),
    }[failure]
    site = _FinalFallback(endpoint, [error, error])
    with pytest.raises(expected):
        site.get_video_data(_VIDEO, _request())
    assert [name for name, _ in site.posts].count(endpoint) == 2
    assert len(site.posts) == (2 if endpoint == "player" else 3)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (http_response(status_code=401), HTTPError),
        (http_response(status_code=403), HTTPError),
        (http_response(status_code=404), VideoNotFound),
        (
            http_response(status_code=429, text="captcha challenge"),
            CaptchaChallengeRequired,
        ),
    ],
)
def test_final_fallback_does_not_retry_terminal_status(response, expected):
    site = _FinalFallback("player", [response])
    with pytest.raises(expected):
        site.get_video_data(_VIDEO, _request())
    assert len(site.posts) == 1


@pytest.mark.parametrize("code", [401, 404])
def test_final_fallback_does_not_retry_terminal_api_error(code):
    payload = _failures()["terminal_error"]
    payload["error"]["code"] = code
    site = _FinalFallback("player", [http_response(payload=payload)])
    with pytest.raises(VideoNotFound if code == 404 else HTTPError):
        site.get_video_data(_VIDEO, _request())
    assert len(site.posts) == 1


@pytest.mark.parametrize("endpoint", ["player", "next"])
@pytest.mark.parametrize("code", [429, 503])
def test_api_challenge_stops_bootstrap_without_retry(endpoint, code):
    response = http_response(
        payload={"error": {"code": code, "message": "captcha challenge"}}
    )
    site = _FinalFallback(endpoint, [response])
    with pytest.raises(CaptchaChallengeRequired):
        site.get_video_data(_VIDEO, _request())
    assert [name for name, _ in site.posts].count(endpoint) == 1


@pytest.mark.parametrize("endpoint", ["player", "next"])
def test_sorry_index_anchor_403_stops_bootstrap_after_one_attempt(endpoint):
    site = _FinalFallback(
        endpoint,
        [http_response(status_code=403, text='<a href="/sorry/index">Continue</a>')],
    )
    with pytest.raises(CaptchaChallengeRequired) as caught:
        site.get_video_data(_VIDEO, _request())
    assert [name for name, _ in site.posts].count(endpoint) == 1
    assert "HTTP 403" in str(caught.value)
    assert f"/youtubei/v1/{endpoint}" in str(caught.value)
    assert "--request_profile" in str(caught.value)


@pytest.mark.parametrize("endpoint", ["player", "next"])
def test_sorry_index_api_message_stops_bootstrap_after_one_attempt(endpoint):
    site = _FinalFallback(
        endpoint,
        [http_response(payload={"error": {"code": 403, "message": "/sorry/index"}})],
    )
    with pytest.raises(CaptchaChallengeRequired) as caught:
        site.get_video_data(_VIDEO, _request())
    assert [name for name, _ in site.posts].count(endpoint) == 1
    assert "/sorry/index" in str(caught.value)
    assert f"/youtubei/v1/{endpoint}" in str(caught.value)


class _Enrichment(YouTubeVideoInitializationMixin):
    def __init__(self, responses):
        self._request_diagnostics = {}
        self._session_get = Mock(side_effect=responses)

    def _parse_video_data(self, *_args):
        return (
            {"status": "live", "continuation_info": {}},
            {"playabilityStatus": {"status": "OK"}},
            watch_chat(
                {"continuations": [submenu_item("client-token")["continuation"]]}
            ),
            {},
        )


def _enrichment_response():
    data = json.dumps(_failures()["enrichment"])
    return http_response(text=f"<script>var ytInitialData = {data};</script>")


@pytest.mark.parametrize("failure", ["503", "timeout", "json", "null", "incomplete"])
def test_enrichment_retries_before_returning_the_semantic_view(failure):
    bad = {
        "503": http_response(status_code=503),
        "timeout": Timeout("offline"),
        "json": http_response(text="var ytInitialData = {broken};"),
        "null": http_response(text="var ytInitialData = null;"),
        "incomplete": http_response(text="var ytInitialData = {};"),
    }[failure]
    site = _Enrichment([bad, _enrichment_response()])
    details, config = site._get_initial_video_info(_VIDEO, _request())
    assert details["continuation_info"] == {"Live chat": "retry-live"}
    assert site._session_get.call_count == 2
    assert all(
        "continuation=client-token" in c.args[0]
        for c in site._session_get.call_args_list
    )
    assert (
        config["_chat_downloader_bootstrap_diagnostics"]["bootstrap_request_count"] == 2
    )


@pytest.mark.parametrize("failure", ["503", "timeout", "incomplete"])
def test_enrichment_exhaustion_is_bounded(failure):
    bad, expected = {
        "503": (http_response(status_code=503), HTTPError),
        "timeout": (Timeout("offline"), Timeout),
        "incomplete": (http_response(text="var ytInitialData = {};"), NoChatReplay),
    }[failure]
    site = _Enrichment([bad, bad])
    with pytest.raises(expected):
        site._get_initial_video_info(_VIDEO, _request())
    assert site._session_get.call_count == 2


@pytest.mark.parametrize("status", [401, 403, 404])
def test_enrichment_does_not_retry_terminal_status(status):
    site = _Enrichment([http_response(status_code=status)])
    with pytest.raises(VideoNotFound if status == 404 else HTTPError):
        site._get_initial_video_info(_VIDEO, _request())
    assert site._session_get.call_count == 1


@pytest.mark.parametrize("operation", ["final", "enrichment"])
def test_bootstrap_retry_honors_server_minimum(monkeypatch, operation):
    sleeps = []
    monkeypatch.setattr(retry_utils.time, "sleep", sleeps.append)
    bad = http_response(status_code=503)
    bad.headers = {"Retry-After": "7"}
    if operation == "final":
        site = _FinalFallback("next", [bad, None])
        site.get_video_data(_VIDEO, _request())
    else:
        site = _Enrichment([bad, _enrichment_response()])
        site._get_initial_video_info(_VIDEO, _request())
    assert sleeps == [7]


@pytest.mark.parametrize("operation", ["final", "enrichment"])
def test_bootstrap_cancellation_stops_before_a_second_request(operation):
    stopped = threading.Event()

    def stop(_url, **_kwargs):
        stopped.set()
        raise Timeout("offline")

    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        if operation == "final":
            site = _FinalFallback("player", [])
            site._session_post = Mock(side_effect=stop)
            with pytest.raises(_PollingCancelled):
                site.get_video_data(_VIDEO, _request())
            assert site._session_post.call_count == 1
        else:
            site = _Enrichment([])
            site._session_get.side_effect = stop
            with pytest.raises(_PollingCancelled):
                site._get_initial_video_info(_VIDEO, _request())
            assert site._session_get.call_count == 1
    finally:
        _POLLING_STOP_EVENT.reset(token)


@pytest.mark.parametrize("operation", ["final", "enrichment"])
@pytest.mark.parametrize("status", [401, 429, 503])
def test_response_bearing_curl_error_preserves_status_and_server_delay(
    monkeypatch, operation, status
):
    delays = []
    monkeypatch.setattr(retry_utils.time, "sleep", delays.append)
    response = http_response(status_code=status)
    response.headers = {"Retry-After": "7"}
    error = CurlHTTPError("synthetic HTTP failure", response=response)
    if operation == "final":
        site = _FinalFallback("player", [error, None])

        def call():
            return site.get_video_data(_VIDEO, _request())
    else:
        site = _Enrichment([error, _enrichment_response()])

        def call():
            return site._get_initial_video_info(_VIDEO, _request())

    if status == 401:
        with pytest.raises(CurlHTTPError):
            call()
        assert delays == []
    else:
        result = call()
        details = result if operation == "final" else result[0]
        assert details["continuation_info"]["Live chat"]
        assert delays == [7]


@pytest.mark.parametrize("operation", ["final", "enrichment"])
@pytest.mark.parametrize("invalid_json", [False, True])
def test_bootstrap_parse_failure_retains_response_minimum(
    monkeypatch, operation, invalid_json
):
    delays = []
    monkeypatch.setattr(retry_utils.time, "sleep", delays.append)
    if operation == "final":
        bad = http_response(payload=None)
        if invalid_json:
            bad.json.side_effect = ValueError("invalid JSON")
        bad.headers = {"Retry-After": "7"}
        site = _FinalFallback("player", [bad, None])
        details = site.get_video_data(_VIDEO, _request())
    else:
        bad = http_response(
            text="<script>var ytInitialData = "
            + ("{broken" if invalid_json else "null")
            + ";</script>"
        )
        bad.headers = {"Retry-After": "7"}
        site = _Enrichment([bad, _enrichment_response()])
        details, _ = site._get_initial_video_info(_VIDEO, _request())
    assert details["continuation_info"]["Live chat"]
    assert delays == [7]


def test_bootstrap_does_not_reuse_previous_response_minimum(monkeypatch):
    delays = []
    monkeypatch.setattr(retry_utils.time, "sleep", delays.append)
    bad = http_response(payload=None)
    bad.headers = {"Retry-After": "7"}
    site = _FinalFallback("player", [bad, Timeout("no response"), None])
    details = site.get_video_data(_VIDEO, ChatRequest(max_attempts=3, retry_timeout=0))
    assert details["continuation_info"]["Live chat"]
    assert delays == [7, 0]

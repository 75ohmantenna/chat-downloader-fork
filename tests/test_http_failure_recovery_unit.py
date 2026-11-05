# SPDX-License-Identifier: MIT

"""Offline request-to-retry contracts shared by provider consumers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

from chat_downloader.errors import IncompleteContinuationError, RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites import retry as site_retry
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.errors import KickServerError
from chat_downloader.sites.kick.request_retry import fetch_with_retry
from chat_downloader.sites.twitch.graphql_client import _download_base_gql
from chat_downloader.sites.twitch.replay_service import _fetch_gql_one
from chat_downloader.sites.youtube.client_requests_continuation import (
    _get_continuation_info,
)
from chat_downloader.sites.youtube.client_requests_initial import _get_initial_info
from chat_downloader.sites.youtube.continuation_helpers import _resolve_poll_delay_ms
from chat_downloader.sites.youtube.continuations import parse_continuation_response
from chat_downloader.utils import retry_utils
from tests.kick_helpers import FakeKickSession

_FIXTURE = Path(__file__).parent / (
    "fixtures/youtube/continuations/failure-responses-synthetic.json"
)
_FAILURES = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_SUCCESS = {"continuationContents": {"liveChatContinuation": {"actions": []}}}
_URL = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"


def _response(status, body, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response.headers.update(headers or {})
    response.url = _URL
    return response


@pytest.fixture
def sleeps(monkeypatch):
    delays = []
    monkeypatch.setattr(retry_utils.time, "sleep", delays.append)
    monkeypatch.setattr(site_retry, "polling_sleep", delays.append)
    monkeypatch.setattr(retry_utils.time, "time", lambda: 1445412420.0)
    return delays


@pytest.mark.parametrize("case", _FAILURES["responses"][4:])
@pytest.mark.parametrize("browse", [False, True])
def test_malformed_roots_and_error_containers_recover_before_consumption(
    case, browse, sleeps
):
    expected = {} if browse else _SUCCESS
    post = Mock(
        side_effect=[
            _response(case["status"], case["body"]),
            _response(200, expected),
        ]
    )
    assert (
        _get_continuation_info(
            _URL,
            post,
            ChatRequest(max_attempts=2, retry_timeout=0),
            require_live_chat_continuation=not browse,
        )
        == expected
    )
    assert post.call_count == 2
    assert sleeps == [0.0]


@pytest.mark.parametrize("root", [None, [], "unexpected", 17, True])
def test_malformed_successful_json_root_exhausts_as_incomplete(root, sleeps):
    post = Mock(return_value=_response(200, root))
    with pytest.raises(IncompleteContinuationError, match="Expected a JSON object"):
        _get_continuation_info(_URL, post, ChatRequest(max_attempts=2, retry_timeout=0))
    assert post.call_count == 2
    assert sleeps == [0.0]


@pytest.mark.parametrize(
    "raw", [float("inf"), float("-inf"), float("nan"), "1e10000", "9" * 5000]
)
def test_unusable_poll_delay_keeps_continuation_and_uses_existing_default(raw):
    payload = _FAILURES["poll_delay_template"]
    payload = json.loads(json.dumps(payload))
    info = payload["continuationContents"]["liveChatContinuation"]
    info["continuations"][0]["timedContinuationData"]["timeoutMs"] = raw
    result = parse_continuation_response(payload)
    assert result.next_continuation == "next"
    assert result.timeout_ms is None
    assert _resolve_poll_delay_ms(result.timeout_ms) == 5000
    assert _resolve_poll_delay_ms(raw) == 5000
    assert _resolve_poll_delay_ms(raw, replay_poll_interval=0.5) == 500


def _fetch(provider, post, request):
    if provider == "youtube":
        return _get_continuation_info(_URL, post, request)
    if provider == "twitch":
        return _fetch_gql_one(
            SimpleNamespace(retry=site_retry.retry),
            lambda: _download_base_gql(post, []),
            request,
        )
    client = KickApiClient(session=SimpleNamespace(get=post))
    return fetch_with_retry(lambda: client.fetch_channel("example"), request)


@pytest.mark.parametrize("provider", ["youtube", "twitch", "kick"])
@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("7", 7.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 60.0),
        ("0", 2.0),
        ("Wed, 21 Oct 2015 07:26:00 GMT", 2.0),
        ("invalid", 2.0),
        ("9" * 5000, 2.0),
    ],
)
def test_providers_honor_retry_after_without_overriding_local_backoff(
    provider, status, header, expected, sleeps
):
    post = Mock(
        side_effect=[
            _response(status, {"error": None}, {"Retry-After": header}),
            _response(200, _SUCCESS),
        ]
    )
    result = _fetch(
        provider,
        post,
        ChatRequest(max_attempts=2, retry_timeout=2, interruptible_retry=False),
    )
    assert result == _SUCCESS
    assert post.call_count == 2
    assert sleeps == [expected]


@pytest.mark.parametrize("provider", ["youtube", "twitch", "kick"])
@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_server_hints_do_not_reset_attempt_budgets(provider, attempts, sleeps):
    post = Mock(return_value=_response(503, None, {"Retry-After": "7"}))
    with pytest.raises(RetriesExceeded):
        _fetch(
            provider,
            post,
            ChatRequest(
                max_attempts=attempts, retry_timeout=0, interruptible_retry=False
            ),
        )
    assert post.call_count == attempts
    assert sleeps == [7.0] * (attempts - 1)


@pytest.mark.parametrize("status", [403, 429, 500, 503, 599])
def test_youtube_initial_page_forwards_retry_after(status, sleeps):
    page = _response(200, {})
    page._content = b'<script id="yt-initial-data">{"contents":{}}</script>'
    get = Mock(side_effect=[_response(status, {}, {"Retry-After": "7"}), page])
    assert _get_initial_info(
        "https://www.youtube.com/watch?v=example",
        get,
        ChatRequest(max_attempts=2, retry_timeout=0),
        "unmatched-initial",
        "unmatched-config",
        "unmatched-player",
    ) == ({"contents": {}}, {}, {})
    assert get.call_count == 2
    assert sleeps == [7.0]


def test_youtube_json_status_forwards_response_delay(sleeps):
    post = Mock(
        side_effect=[
            _response(
                200,
                {"error": {"code": 429, "message": "Rate limited"}},
                {"Retry-After": "7"},
            ),
            _response(200, _SUCCESS),
        ]
    )
    assert _fetch("youtube", post, ChatRequest(max_attempts=2)) == _SUCCESS
    assert sleeps == [7.0]


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("exception", [requests.HTTPError, CurlHTTPError])
def test_raised_http_errors_preserve_delay_across_request_boundaries(
    initial, exception, sleeps
):
    response = _response(503, None, {"Retry-After": "7"})
    error = exception("temporary failure", response=response)
    success = _response(200, _SUCCESS)
    if initial:
        success._content = b'<script id="yt-initial-data">{"contents":{}}</script>'
    request = Mock(side_effect=[error, success])
    options = ChatRequest(max_attempts=2, retry_timeout=0)
    if initial:
        result = _get_initial_info(
            "https://www.youtube.com/watch?v=example",
            request,
            options,
            "unmatched-initial",
            "unmatched-config",
            "unmatched-player",
        )
        assert result[0] == {"contents": {}}
    else:
        assert _fetch("youtube", request, options) == _SUCCESS
    assert sleeps == [7.0]
    assert request.call_count == 2


def test_kick_transient_error_keeps_only_sanitized_delay():
    response = _response(429, {}, {"Retry-After": "7", "Set-Cookie": "secret"})
    client = KickApiClient(session=FakeKickSession([response]))
    with pytest.raises(KickServerError) as caught:
        client.fetch_channel("example")
    assert vars(caught.value) == {"retry_after": 7.0}
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("error", [None, [], "not-an-object", 17])
def test_http_retry_tolerates_all_malformed_error_containers(status, error, sleeps):
    post = Mock(
        side_effect=[
            _response(status, {"error": error}),
            _response(200, _SUCCESS),
        ]
    )
    assert (
        _fetch("youtube", post, ChatRequest(max_attempts=2, retry_timeout=0))
        == _SUCCESS
    )
    assert post.call_count == 2
    assert sleeps == [0.0]


@pytest.mark.parametrize("attempts", [1, 2])
def test_initial_page_server_hint_does_not_extend_attempt_budget(attempts, sleeps):
    get = Mock(return_value=_response(503, {}, {"Retry-After": "7"}))
    with pytest.raises(RetriesExceeded):
        _get_initial_info(
            "https://www.youtube.com/watch?v=example",
            get,
            ChatRequest(max_attempts=attempts, retry_timeout=0),
            "unmatched-initial",
            "unmatched-config",
            "unmatched-player",
        )
    assert get.call_count == attempts
    assert sleeps == [7.0] * (attempts - 1)


@pytest.mark.parametrize("payload", [{}, {"error": {"message": "unknown error"}}])
def test_incomplete_chat_response_preserves_server_minimum(payload, sleeps):
    post = Mock(
        side_effect=[
            _response(200, payload, {"Retry-After": "7"}),
            _response(200, _SUCCESS),
        ]
    )
    assert (
        _fetch("youtube", post, ChatRequest(max_attempts=2, retry_timeout=0))
        == _SUCCESS
    )
    assert sleeps == [7.0]


@pytest.mark.parametrize("reconnect", [False, True])
def test_wrapped_kick_server_error_delays_retry_and_reconnect(reconnect, sleeps):
    outer = ConnectionError("anonymous connection failed")
    outer.__cause__ = KickServerError("rate limited", retry_after=7)
    request = ChatRequest(max_attempts=2, retry_timeout=0)
    if reconnect:
        site_retry.wait_for_reconnect(1, error=outer, request=request, provider="Kick")
    else:
        site_retry.retry(1, error=outer, request=request)
    assert sleeps == [7.0]


def test_error_cause_cycle_without_a_hint_terminates():
    first, second = ConnectionError("first"), ConnectionError("second")
    first.__cause__, second.__cause__ = second, first
    assert retry_utils.get_retry_after(first) is None


@pytest.mark.parametrize(
    ("provider", "invalid_json"),
    [
        ("youtube", False),
        ("kick", False),
        ("youtube", True),
        ("kick", True),
        ("twitch", True),
    ],
)
def test_malformed_success_body_retains_response_minimum(
    provider, invalid_json, sleeps
):
    bad = _response(200, None, {"Retry-After": "7"})
    if invalid_json:
        bad._content = b"{truncated"
    post = Mock(side_effect=[bad, _response(200, _SUCCESS)])
    assert (
        _fetch(provider, post, ChatRequest(max_attempts=2, retry_timeout=0)) == _SUCCESS
    )
    assert sleeps == [7]

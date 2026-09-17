# SPDX-License-Identifier: MIT

from __future__ import annotations

from json import JSONDecodeError
from types import SimpleNamespace

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import chat_downloader.sites.youtube.client_context as _yt_context
import chat_downloader.sites.youtube.client_requests_continuation as _yt_continuation
import chat_downloader.sites.youtube.client_requests_initial as _yt_initial
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.models import ChatRequest

_SUCCESS_CONTINUATION_PAYLOAD = {
    "continuationContents": {"liveChatContinuation": {"actions": []}},
}
_WATCH_URL = "https://www.youtube.com/watch?v=test"


def _initial(session_get, params):
    return _yt_initial._get_initial_info(
        _WATCH_URL,
        session_get,
        params,
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )


def _continuation(session_post, params, *, browse=False):
    endpoint = "browse" if browse else "live_chat/get_live_chat"
    return _yt_continuation._get_continuation_info(
        f"https://www.youtube.com/youtubei/v1/{endpoint}",
        session_post,
        params,
        require_live_chat_continuation=not browse,
        json={"continuation": "abc"},
    )


def _page(status, text):
    return SimpleNamespace(status_code=status, text=text)


def _sequence(*responses):
    pending = iter(responses)
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        response = next(pending)
        if isinstance(response, Exception):
            raise response
        return response

    return request, calls


def _patch_initial_parser(monkeypatch, title, *, valid=True):
    monkeypatch.setattr(
        _yt_initial, "regex_search", lambda *_a, **_k: "{}" if valid else None
    )
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: (
            {"contents": {}} if valid and default is None else default
        ),
    )
    monkeypatch.setattr(_yt_initial, "get_title_of_webpage", lambda _html: title)


def test_get_innertube_context_normalizes_without_mutating_input() -> None:
    ytcfg = {
        "INNERTUBE_CONTEXT": {
            "client": {
                "hl": "fr",
                "timeZone": "Europe/Paris",
                "utcOffsetMinutes": 60,
                "visitorData": "visitor",
            },
        },
    }
    context = _yt_context._get_innertube_context(ytcfg)
    assert context["client"]["hl"] == "en"
    assert context["client"]["timeZone"] == "UTC"
    assert context["client"]["utcOffsetMinutes"] == 0
    assert context["client"]["visitorData"] == "visitor"
    assert ytcfg["INNERTUBE_CONTEXT"]["client"]["hl"] == "fr"
    assert ytcfg["INNERTUBE_CONTEXT"]["client"]["timeZone"] == "Europe/Paris"


def test_generate_headers_includes_user_agent_and_bootstrap_logged_in() -> None:
    ytcfg = {
        "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
        "INNERTUBE_CLIENT_VERSION": "1.20260101.00.00",
        "LOGGED_IN": True,
        "INNERTUBE_CONTEXT": {
            "client": {"visitorData": "visitor123", "userAgent": "TestYTUA/1.0"},
        },
    }
    headers = _yt_context._generate_headers(
        ytcfg=ytcfg,
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: "AUTH",
    )
    assert headers["x-goog-visitor-id"] == "visitor123"
    assert headers["user-agent"] == "TestYTUA/1.0"
    assert headers["x-youtube-bootstrap-logged-in"] == "true"
    assert headers["authorization"] == "AUTH"


@pytest.mark.parametrize(
    ("status", "payload", "typed_request"),
    [
        pytest.param(
            429,
            {"error": {"code": 429, "message": "Too Many Requests"}},
            False,
            id="http-429",
        ),
        pytest.param(
            429,
            {"error": {"code": 429, "message": "Too Many Requests"}},
            True,
            id="chat-request",
        ),
        pytest.param(200, {"responseContext": {}}, False, id="incomplete-body"),
        pytest.param(
            200,
            {"error": {"code": 500, "message": "Unknown error"}},
            False,
            id="json-error",
        ),
        pytest.param(None, OSError("network unreachable"), False, id="oserror"),
    ],
)
def test_get_continuation_info_retries(
    make_fake_http_response, status, payload, typed_request
):
    first = payload if status is None else make_fake_http_response(status, payload)
    request, calls = _sequence(
        first, make_fake_http_response(200, _SUCCESS_CONTINUATION_PAYLOAD)
    )
    params = (
        ChatRequest(url=_WATCH_URL, max_attempts=2)
        if typed_request
        else {"max_attempts": 2, "retry_timeout": 0}
    )
    assert _continuation(request, params) == _SUCCESS_CONTINUATION_PAYLOAD
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("status", "text", "title", "typed_request"),
    [
        (500, "<html>server error</html>", "Server Error", False),
        (
            429,
            "<html><title>Too Many Requests</title></html>",
            "Too Many Requests",
            False,
        ),
        (500, "<html>server error</html>", "Server Error", True),
    ],
    ids=["http-500", "http-429", "chat-request"],
)
def test_get_initial_info_retries(monkeypatch, status, text, title, typed_request):
    request, calls = _sequence(_page(status, text), _page(200, "<html>ok</html>"))
    _patch_initial_parser(monkeypatch, title)
    params = (
        ChatRequest(url=_WATCH_URL, max_attempts=2)
        if typed_request
        else {"max_attempts": 2, "retry_timeout": 0}
    )
    assert _initial(request, params) == ({"contents": {}}, {}, {})
    assert len(calls) == 2


def test_get_initial_info_raises_challenge_on_sorry_page(monkeypatch) -> None:
    response = _page(
        429,
        "<html><body>Our systems have detected unusual traffic from your "
        "computer network. <div class='g-recaptcha'></div></body></html>",
    )
    response.url = "https://www.google.com/sorry/index?continue=..."
    monkeypatch.setattr(_yt_initial, "get_title_of_webpage", lambda _html: _WATCH_URL)
    with pytest.raises(CaptchaChallengeRequired) as exc_info:
        _initial(lambda _url: response, {"max_attempts": 2, "retry_timeout": 0})
    assert "captcha/challenge" in str(exc_info.value)
    assert "--request_profile" in str(exc_info.value)


def test_get_initial_info_raises_retries_exceeded_when_attempt_loop_exits(monkeypatch):
    request, calls = _sequence()
    monkeypatch.setattr(
        ChatRequest,
        "from_kwargs",
        classmethod(
            lambda _cls, **_kwargs: SimpleNamespace(max_attempts=0, retry_timeout=None)
        ),
    )
    with pytest.raises(RetriesExceeded) as exc_info:
        _initial(request, {})
    assert calls == []
    assert "Retries exhausted after 0 attempt(s)" in str(exc_info.value)


@pytest.mark.parametrize(
    ("status", "payload", "attempts", "error", "fragments"),
    [
        pytest.param(
            429,
            {},
            2,
            RetriesExceeded,
            ["Retries exhausted", "2 attempt(s)", "live_chat"],
            id="http-429",
        ),
        pytest.param(
            500,
            {},
            1,
            RetriesExceeded,
            ["Retries exhausted", "1 attempt(s)"],
            id="http-500",
        ),
        pytest.param(
            200,
            {"error": {"code": 429, "message": "Rate limited"}},
            2,
            RetriesExceeded,
            ["Retries exhausted", "Rate limited"],
            id="json-429",
        ),
        pytest.param(
            200,
            {"responseContext": {}},
            1,
            IncompleteContinuationError,
            [
                "Missing continuationContents.liveChatContinuation",
                "Summary:",
                "top_level_keys",
            ],
            id="incomplete-body",
        ),
    ],
)
def test_get_continuation_info_exhausted(
    make_fake_http_response, status, payload, attempts, error, fragments
):
    def request(_url, **_kwargs):
        return make_fake_http_response(status, payload)

    with pytest.raises(error) as exc_info:
        _continuation(request, {"max_attempts": attempts, "retry_timeout": 0})
    for fragment in fragments:
        assert fragment in str(exc_info.value)


def test_get_continuation_info_handles_json_decode_before_response() -> None:
    request, _ = _sequence(JSONDecodeError("bad json", "", 0))
    with pytest.raises(RetriesExceeded) as exc_info:
        _continuation(request, {"max_attempts": 1})
    assert "Unable to parse JSON" in str(exc_info.value)


@pytest.mark.parametrize(
    ("payload", "browse"),
    [
        pytest.param(
            {"error": {"code": 400, "message": "Replay disabled"}},
            False,
            id="non-retryable-api-error",
        ),
        pytest.param(
            {
                "onResponseReceivedActions": [
                    {"appendContinuationItemsAction": {"continuationItems": []}}
                ]
            },
            True,
            id="browse",
        ),
    ],
)
def test_get_continuation_info_returns_payload(
    make_fake_http_response, payload, browse
):
    assert (
        _continuation(
            lambda _url, **_kwargs: make_fake_http_response(200, payload),
            {"max_attempts": 1, "retry_timeout": 0},
            browse=browse,
        )
        == payload
    )


@pytest.mark.parametrize(
    ("status", "payload", "response_kwargs"),
    [
        pytest.param(
            429,
            {"error": {"code": 429, "message": "Too Many Requests"}},
            {"text": "<html>captcha challenge required</html>"},
            id="http-challenge",
        ),
        pytest.param(
            200,
            {"error": {"code": 403, "message": "Please verify you are human"}},
            {},
            id="json-challenge",
        ),
    ],
)
def test_get_continuation_info_raises_challenge(
    make_fake_http_response, status, payload, response_kwargs
):
    with pytest.raises(CaptchaChallengeRequired) as exc_info:
        _continuation(
            lambda _url, **_kwargs: make_fake_http_response(
                status, payload, **response_kwargs
            ),
            {"max_attempts": 1, "retry_timeout": 0},
        )
    if response_kwargs:
        assert "--request_profile" in str(exc_info.value)


@pytest.mark.parametrize(
    ("status", "text", "title", "fragments"),
    [
        (
            503,
            "<html>service unavailable</html>",
            "503 Error",
            ["Last error: 503 Error"],
        ),
        (
            429,
            "<html><title>Too Many Requests</title></html>",
            "Too Many Requests",
            ["Retries exhausted", "Too Many Requests"],
        ),
    ],
)
def test_get_initial_info_exhausted(monkeypatch, status, text, title, fragments):
    request, calls = _sequence(_page(status, text), _page(status, text))
    if status == 503:
        _patch_initial_parser(monkeypatch, title, valid=False)
    else:
        monkeypatch.setattr(_yt_initial, "get_title_of_webpage", lambda _html: title)
    with pytest.raises(RetriesExceeded) as exc_info:
        _initial(request, {"max_attempts": 2, "retry_timeout": 0})
    assert len(calls) == 2
    for fragment in fragments:
        assert fragment in str(exc_info.value)


def test_get_initial_info_raises_retries_exceeded_on_network_error() -> None:
    request, _ = _sequence(RequestsConnectionError("connection refused"))
    with pytest.raises(RequestsConnectionError):
        _initial(request, {"max_attempts": 1})

# SPDX-License-Identifier: MIT

from __future__ import annotations

from json import JSONDecodeError
from types import SimpleNamespace
from typing import NoReturn

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

# Convenience aliases that mirror the old ``client`` facade names.
_get_innertube_context = _yt_context._get_innertube_context
_generate_headers = _yt_context._generate_headers
_get_continuation_info = _yt_continuation._get_continuation_info
_get_initial_info = _yt_initial._get_initial_info


class _PageResp:
    def __init__(self, status_code, text) -> None:
        self.status_code = status_code
        self.text = text


_SUCCESS_CONTINUATION_PAYLOAD = {
    "continuationContents": {"liveChatContinuation": {"actions": []}},
}


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

    context = _get_innertube_context(ytcfg)

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
            "client": {
                "visitorData": "visitor123",
                "userAgent": "TestYTUA/1.0",
            },
        },
    }

    headers = _generate_headers(
        ytcfg=ytcfg,
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: "AUTH",
    )

    assert headers["x-goog-visitor-id"] == "visitor123"
    assert headers["user-agent"] == "TestYTUA/1.0"
    assert headers["x-youtube-bootstrap-logged-in"] == "true"
    assert headers["authorization"] == "AUTH"


def test_get_continuation_info_retries_on_429(make_fake_http_response) -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return make_fake_http_response(
                429, {"error": {"code": 429, "message": "Too Many Requests"}}
            )
        return make_fake_http_response(200, _SUCCESS_CONTINUATION_PAYLOAD)

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "abc"},
    )

    assert result == _SUCCESS_CONTINUATION_PAYLOAD
    assert calls["count"] == 2


def test_get_continuation_info_accepts_chat_request(
    make_fake_http_response,
) -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return make_fake_http_response(
                429, {"error": {"code": 429, "message": "Too Many Requests"}}
            )
        return make_fake_http_response(200, _SUCCESS_CONTINUATION_PAYLOAD)

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        ChatRequest(url="https://www.youtube.com/watch?v=abc", max_attempts=2),
        json={"continuation": "abc"},
    )

    assert result == _SUCCESS_CONTINUATION_PAYLOAD
    assert calls["count"] == 2


def test_get_continuation_info_retries_on_incomplete_live_chat_continuation_body(
    make_fake_http_response,
) -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return make_fake_http_response(200, {"responseContext": {}})
        return make_fake_http_response(
            200,
            {"continuationContents": {"liveChatContinuation": {"actions": []}}},
        )

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2, "retry_timeout": 0},
        json={"continuation": "abc"},
    )

    assert result == {"continuationContents": {"liveChatContinuation": {"actions": []}}}
    assert calls["count"] == 2


def test_get_continuation_info_retries_on_unknown_error_in_200_ok_body(
    make_fake_http_response,
) -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return make_fake_http_response(
                200, {"error": {"code": 500, "message": "Unknown error"}}
            )
        return make_fake_http_response(
            200,
            {"continuationContents": {"liveChatContinuation": {"actions": []}}},
        )

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2, "retry_timeout": 0},
        json={"continuation": "abc"},
    )

    assert result == {"continuationContents": {"liveChatContinuation": {"actions": []}}}
    assert calls["count"] == 2


def test_get_initial_info_retries_on_5xx_with_one_based_attempts(
    monkeypatch,
) -> None:
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        if calls["count"] == 1:
            return _PageResp(500, "<html>server error</html>")
        return _PageResp(200, "<html>ok</html>")

    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    monkeypatch.setattr(
        _yt_initial, "get_title_of_webpage", lambda _html: "Server Error"
    )

    yt_initial_data, ytcfg, player_response_info = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        session_get,
        {"max_attempts": 2},
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )

    assert calls["count"] == 2
    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {}
    assert player_response_info == {}


def test_get_initial_info_retries_on_429(monkeypatch) -> None:
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        if calls["count"] == 1:
            return _PageResp(429, "<html><title>Too Many Requests</title></html>")
        return _PageResp(200, "<html>ok</html>")

    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    monkeypatch.setattr(
        _yt_initial,
        "get_title_of_webpage",
        lambda _html: "Too Many Requests",
    )

    yt_initial_data, ytcfg, player_response_info = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        session_get,
        {"max_attempts": 2, "retry_timeout": 0},
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )

    assert calls["count"] == 2
    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {}
    assert player_response_info == {}


def test_get_initial_info_raises_challenge_on_sorry_page(
    monkeypatch,
) -> None:
    def session_get(_url):
        response = _PageResp(
            429,
            "<html><body>Our systems have detected unusual traffic from your "
            "computer network. <div class='g-recaptcha'></div></body></html>",
        )
        response.url = "https://www.google.com/sorry/index?continue=..."
        return response

    monkeypatch.setattr(
        _yt_initial,
        "get_title_of_webpage",
        lambda _html: "https://www.youtube.com/watch?v=test",
    )

    with pytest.raises(CaptchaChallengeRequired) as exc_info:
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            session_get,
            {"max_attempts": 2, "retry_timeout": 0},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )

    msg = str(exc_info.value)
    assert "captcha/challenge" in msg
    assert "--request_profile" in msg


def test_get_initial_info_accepts_chat_request(monkeypatch) -> None:
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        if calls["count"] == 1:
            return _PageResp(500, "<html>server error</html>")
        return _PageResp(200, "<html>ok</html>")

    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    monkeypatch.setattr(
        _yt_initial, "get_title_of_webpage", lambda _html: "Server Error"
    )

    yt_initial_data, ytcfg, player_response_info = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        session_get,
        ChatRequest(url="https://www.youtube.com/watch?v=test", max_attempts=2),
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )

    assert calls["count"] == 2
    assert yt_initial_data == {"contents": {}}
    assert ytcfg == {}
    assert player_response_info == {}


def test_get_initial_info_raises_retries_exceeded_when_attempt_loop_exits(
    monkeypatch,
) -> None:
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        return _PageResp(200, "{}")

    monkeypatch.setattr(
        ChatRequest,
        "from_kwargs",
        classmethod(
            lambda _cls, **_kwargs: SimpleNamespace(
                max_attempts=0,
                retry_timeout=None,
            ),
        ),
    )

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            session_get,
            {},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )

    assert calls["count"] == 0
    assert "Retries exhausted after 0 attempt(s)" in str(exc_info.value)


def test_get_continuation_info_raises_retries_exceeded_on_exhausted_429(
    make_fake_http_response,
) -> None:
    """HTTP 429 raises RetriesExceeded after all retries are exhausted."""

    def session_post(_url, **_kwargs):
        return make_fake_http_response(429, {})

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 2},
            json={"continuation": "abc"},
        )

    msg = str(exc_info.value)
    assert "Retries exhausted" in msg
    assert "2 attempt(s)" in msg
    assert "live_chat" in msg


def test_get_continuation_info_raises_retries_exceeded_on_exhausted_5xx(
    make_fake_http_response,
) -> None:
    """HTTP 500 raises RetriesExceeded after all retries are exhausted."""

    def session_post(_url, **_kwargs):
        return make_fake_http_response(500, {})

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1},
            json={"continuation": "abc"},
        )

    msg = str(exc_info.value)
    assert "Retries exhausted" in msg
    assert "1 attempt(s)" in msg


def test_get_continuation_info_raises_retries_exceeded_on_exhausted_json_429(
    make_fake_http_response,
) -> None:
    """HTTP 200 with JSON error 429 raises RetriesExceeded after retries."""

    def session_post(_url, **_kwargs):
        return make_fake_http_response(
            200, {"error": {"code": 429, "message": "Rate limited"}}
        )

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 2},
            json={"continuation": "tok"},
        )

    msg = str(exc_info.value)
    assert "Retries exhausted" in msg
    assert "Rate limited" in msg


def test_get_continuation_info_handles_json_decode_before_response() -> None:
    def session_post(_url, **_kwargs):
        raise JSONDecodeError("bad json", "", 0)

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1},
            json={"continuation": "abc"},
        )

    assert "Unable to parse JSON" in str(exc_info.value)


def test_get_continuation_info_returns_non_retryable_json_api_error(
    make_fake_http_response,
) -> None:
    payload = {"error": {"code": 400, "message": "Replay disabled"}}

    def session_post(_url, **_kwargs):
        return make_fake_http_response(200, payload)

    assert (
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1, "retry_timeout": 0},
            json={"continuation": "tok"},
        )
        == payload
    )


def test_get_continuation_info_raises_retries_exceeded_on_exhausted_incomplete_body(
    make_fake_http_response,
) -> None:
    def session_post(_url, **_kwargs):
        return make_fake_http_response(200, {"responseContext": {}})

    with pytest.raises(IncompleteContinuationError) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1, "retry_timeout": 0},
            json={"continuation": "tok"},
        )

    message = str(exc_info.value)
    assert "Missing continuationContents.liveChatContinuation" in message
    assert "Summary:" in message
    assert "top_level_keys" in message


def test_get_continuation_info_allows_browse_continuation_payload(
    make_fake_http_response,
) -> None:
    payload = {
        "onResponseReceivedActions": [
            {"appendContinuationItemsAction": {"continuationItems": []}},
        ],
    }

    def session_post(_url, **_kwargs):
        return make_fake_http_response(200, payload)

    assert (
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/browse",
            session_post,
            {"max_attempts": 1, "retry_timeout": 0},
            require_live_chat_continuation=False,
            json={"continuation": "tok"},
        )
        == payload
    )


def test_get_continuation_info_raises_captcha_challenge_required_on_http_challenge(
    make_fake_http_response,
) -> None:
    def session_post(_url, **_kwargs):
        return make_fake_http_response(
            429,
            {"error": {"code": 429, "message": "Too Many Requests"}},
            text="<html>captcha challenge required</html>",
        )

    with pytest.raises(CaptchaChallengeRequired) as exc_info:
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1, "retry_timeout": 0},
            json={"continuation": "tok"},
        )

    assert "--request_profile" in str(exc_info.value)


def test_get_continuation_info_raises_captcha_challenge_required_on_json_error_message(
    make_fake_http_response,
) -> None:
    def session_post(_url, **_kwargs):
        return make_fake_http_response(
            200,
            {"error": {"code": 403, "message": "Please verify you are human"}},
        )

    with pytest.raises(CaptchaChallengeRequired):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            session_post,
            {"max_attempts": 1},
            json={"continuation": "tok"},
        )


def test_get_initial_info_retries_on_5xx_then_raises_retries_exceeded(
    monkeypatch,
) -> None:
    """_get_initial_info raises RetriesExceeded after exhausted 5xx retries."""
    calls = {"count": 0}

    def session_get(_url):
        calls["count"] += 1
        return _PageResp(503, "<html>service unavailable</html>")

    # Simulate page that produces no parseable ytInitialData
    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: None)
    monkeypatch.setattr(_yt_initial, "try_parse_json", lambda _v, default=None: default)
    monkeypatch.setattr(_yt_initial, "get_title_of_webpage", lambda _h: "503 Error")

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            session_get,
            {"max_attempts": 2},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )

    assert calls["count"] == 2
    assert "Last error: 503 Error" in str(exc_info.value)


def test_get_initial_info_raises_retries_exceeded_on_exhausted_429(
    monkeypatch,
) -> None:
    def session_get(_url):
        return _PageResp(429, "<html><title>Too Many Requests</title></html>")

    monkeypatch.setattr(
        _yt_initial,
        "get_title_of_webpage",
        lambda _html: "Too Many Requests",
    )

    with pytest.raises(RetriesExceeded) as exc_info:
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            session_get,
            {"max_attempts": 2, "retry_timeout": 0},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )

    msg = str(exc_info.value)
    assert "Retries exhausted" in msg
    assert "Too Many Requests" in msg


def test_get_initial_info_raises_retries_exceeded_on_network_error(
    monkeypatch,
) -> None:
    """_get_initial_info re-raises RequestException after all attempts."""

    def session_get(_url) -> NoReturn:
        msg = "connection refused"
        raise RequestsConnectionError(msg)

    with pytest.raises(RequestsConnectionError):
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            session_get,
            {"max_attempts": 1},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


def test_get_continuation_info_catches_oserror_as_network_error(
    make_fake_http_response,
) -> None:
    """_get_continuation_info treats OSError the same as RequestException."""
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            msg = "network unreachable"
            raise OSError(msg)
        return make_fake_http_response(200, _SUCCESS_CONTINUATION_PAYLOAD)

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "abc"},
    )
    assert result == _SUCCESS_CONTINUATION_PAYLOAD
    assert calls["count"] == 2

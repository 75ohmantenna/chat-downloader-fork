# SPDX-License-Identifier: MIT

"""Bounded initial replay profile recovery and content-free visitor logging."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader.errors import ChatDownloaderError, NoChatReplay
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.continuation import _ContinuationLoop
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader

FIXTURES = Path(__file__).parent / "fixtures/youtube"


def _error():
    return json.loads(
        (FIXTURES / "continuations/initial-replay-invalid-argument.json").read_text()
    )


def _loop(provider, *, status="was_live"):
    return _ContinuationLoop(
        provider,
        {"status": status, "continuation_info": {"Live chat": "first"}},
        {"INNERTUBE_API_KEY": "fixture"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=fixture",
            start_time=8300,
            end_time=8400,
            message_groups=["all"],
        ),
    )


def test_initial_replay_400_switches_profile_preserving_token_seek_and_headers(
    monkeypatch,
):
    provider = YouTubeChatDownloader(
        request_profile="youtube_web", headers={"User-Agent": "custom-agent"}
    )
    requests = []
    payload = json.loads(
        (FIXTURES / "live_events/mobile-replay-elements.json").read_text()
    )
    payload["continuationContents"]["liveChatContinuation"]["actions"] = payload[
        "continuationContents"
    ]["liveChatContinuation"]["actions"][1:2]

    def respond(_url, _post, _params, **kwargs):
        requests.append(deepcopy(kwargs["json"]))
        return _error() if provider._request_profile == "youtube_web" else payload

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info", respond
    )
    try:
        messages = list(_loop(provider).run())
        assert provider._request_profile == "youtube_android"
        assert provider.session.headers["User-Agent"] == "custom-agent"
    finally:
        provider.close()
    assert len(requests) == 2
    assert [r["continuation"] for r in requests] == ["first", "first"]
    assert [r["currentPlayerState"]["playerOffsetMs"] for r in requests] == [
        8295000,
        8295000,
    ]
    assert [r["context"]["client"]["clientName"] for r in requests] == [
        "WEB",
        "ANDROID",
    ]
    assert [m["message_type"] for m in messages] == ["text_message", "chat_ended"]


@pytest.mark.parametrize(
    ("fallback", "status", "expected_calls"),
    [(True, "was_live", 3), (False, "was_live", 1), (True, "live", 1)],
)
def test_initial_400_recovery_is_bounded_optional_and_replay_only(
    monkeypatch, fallback, status, expected_calls
):
    provider = YouTubeChatDownloader(
        request_profile="youtube_web", auto_profile_fallback=fallback
    )
    calls = []

    def reject(*args, **kwargs):
        calls.append(provider._request_profile)
        return _error()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info", reject
    )
    try:
        with pytest.raises(
            ChatDownloaderError, match="rejected the chat continuation"
        ) as exc:
            list(_loop(provider, status=status).run())
        assert not isinstance(exc.value, NoChatReplay)
    finally:
        provider.close()
    assert len(calls) == expected_calls


def test_400_after_accepted_response_never_restarts_replay(monkeypatch):
    provider = YouTubeChatDownloader(request_profile="youtube_web")
    responses = iter(
        [
            {
                "continuationContents": {
                    "liveChatContinuation": {
                        "actions": [],
                        "continuations": [
                            {
                                "timedContinuationData": {
                                    "continuation": "second",
                                    "timeoutMs": 500,
                                }
                            }
                        ],
                    }
                }
            },
            _error(),
        ]
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *a, **kw: next(responses),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep", lambda _: None
    )
    try:
        with pytest.raises(ChatDownloaderError, match="rejected the chat continuation"):
            list(_loop(provider).run())
        assert provider._request_profile == "youtube_web"
    finally:
        provider.close()


def test_visitor_state_is_updated_without_logging_its_value(monkeypatch):
    provider = YouTubeChatDownloader()
    logs = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.log",
        lambda *args: logs.append(args),
    )
    try:
        _loop(provider)._handle_continuation_response(
            {"responseContext": {"visitorData": "private-visitor-value"}}, {}
        )
        assert provider.session.headers["x-goog-visitor-id"] == "private-visitor-value"
    finally:
        provider.close()
    assert ("debug", "Updated visitor data") in logs
    assert "private-visitor-value" not in str(logs)

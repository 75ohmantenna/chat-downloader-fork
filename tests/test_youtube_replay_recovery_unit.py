# SPDX-License-Identifier: MIT

"""Bounded initial chat profile recovery and content-free visitor logging."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader.errors import ChatDownloaderError, NoChatReplay, NoContinuation
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.continuation import _ContinuationLoop
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response

FIXTURES = Path(__file__).parent / "fixtures/youtube"


def _error():
    return json.loads(
        (FIXTURES / "continuations/initial-replay-invalid-argument.json").read_text()
    )


def _loop(provider, *, status="was_live"):
    return _ContinuationLoop(
        provider,
        {
            "status": status,
            "original_video_id": "fixture",
            "continuation_info": {"Live chat": "first"},
        },
        {"INNERTUBE_API_KEY": "fixture"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=fixture",
            start_time=8300,
            end_time=8400,
            message_groups=["all"],
        ),
    )


def test_initial_replay_400_refreshes_token_preserving_seek_and_headers(
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
    refresh = []

    def bootstrap(video_id, params):
        refresh.append((video_id, params))
        return {"continuation_info": {"Live chat": "fresh-mobile"}}, {
            "INNERTUBE_API_KEY": "fresh-key",
        }

    monkeypatch.setattr(provider, "_get_initial_video_info", bootstrap)
    try:
        messages = list(_loop(provider).run())
        assert provider._request_profile == "youtube_android"
        assert provider.session.headers["User-Agent"] == "custom-agent"
    finally:
        provider.close()
    assert len(requests) == 2
    assert [r["continuation"] for r in requests] == ["first", "fresh-mobile"]
    assert refresh[0][0] == "fixture"
    assert refresh[0][1].end_time == 8400
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
    [
        (True, "was_live", 3),
        (False, "was_live", 1),
        (True, "live", 3),
        (False, "live", 1),
        (True, "upcoming", 3),
        (False, "upcoming", 1),
        (True, "unknown", 1),
    ],
)
def test_initial_400_recovery_is_bounded_optional_and_requires_known_status(
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
    monkeypatch.setattr(
        provider,
        "_get_initial_video_info",
        lambda *_: (
            {"continuation_info": {"Live chat": f"fresh-{provider._request_profile}"}},
            {"INNERTUBE_API_KEY": "fixture"},
        ),
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


@pytest.mark.parametrize("status", ["was_live", "live", "upcoming"])
def test_400_after_accepted_response_never_restarts_chat(monkeypatch, status):
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
            list(_loop(provider, status=status).run())
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


@pytest.mark.parametrize("view", ["live", "top"])
@pytest.mark.parametrize("status", ["was_live", "live", "upcoming"])
def test_http_400_recovery_keeps_error_ledger_and_reports_success(
    monkeypatch, caplog, view, status
):
    caplog.set_level("INFO", logger="chat_downloader")
    provider = YouTubeChatDownloader(
        request_profile="youtube_web",
        headers={"User-Agent": "custom-agent", "X-Diagnostic": "preserved"},
    )
    payload = json.loads(
        (FIXTURES / "live_events/mobile-replay-elements.json").read_text()
    )
    if status != "was_live":
        payload = json.loads(
            (
                FIXTURES / "live_events/youtube-IzopCEgh2G8-live-chat-first.json"
            ).read_text()
        )
        info = payload["continuationContents"]["liveChatContinuation"]
        info["actions"] = info["actions"][:2]
        del info["continuations"]
    post = Mock(
        side_effect=[
            http_response(status_code=400, payload=_error()),
            http_response(payload=payload),
        ]
    )
    monkeypatch.setattr(provider, "_session_post", post)
    refresh = Mock(
        return_value=(
            {"continuation_info": {"Live chat": "fresh-live", "Top chat": "fresh-top"}},
            {"INNERTUBE_API_KEY": "fresh-key"},
        )
    )
    monkeypatch.setattr(provider, "_get_initial_video_info", refresh)
    ticks = iter([1000, 2000])
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.get_live_start_time_ms",
        lambda: next(ticks),
    )
    loop = _loop(provider, status=status)
    loop.params = loop.params.with_updates(chat_type=view)
    loop.initial_info["continuation_info"]["Top chat"] = "first-top"
    try:
        assert list(loop.run())
        assert provider.session.headers["User-Agent"] == "custom-agent"
        assert provider.session.headers["X-Diagnostic"] == "preserved"
    finally:
        provider.close()
    assert post.call_count == 2
    assert post.call_args.kwargs["json"]["continuation"] == f"fresh-{view}"
    assert "fresh-key" in post.call_args.args[0]
    assert post.call_args.kwargs["json"]["context"]["client"]["clientName"] == "ANDROID"
    assert refresh.call_args.args[1].end_time == 8400
    assert loop.diagnostics["http_error_count"] == 1
    assert loop.diagnostics["continuation_request_count"] == 2
    kind = "replay" if status == "was_live" else "live"
    assert loop.diagnostics[f"initial_{kind}_recovery_attempt_count"] == 1
    assert loop.diagnostics[f"initial_{kind}_recovery_success_count"] == 1
    assert loop.diagnostics["chat_view"] == f"{view.title()} chat" + (
        " replay" if kind == "replay" else ""
    )
    assert loop.ctx.live_start_time_ms == 1000
    assert loop.diagnostics.get("parse_error", 0) == 0
    assert "youtube_web was rejected" in caplog.text
    assert "recovery succeeded with profile: youtube_android" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize("status", ["was_live", "live"])
def test_malformed_replacement_response_never_counts_as_recovery_success(
    monkeypatch, status
):
    from chat_downloader.errors import IncompleteContinuationError

    provider = YouTubeChatDownloader(request_profile="youtube_web")
    responses = iter(
        [_error(), {"continuationContents": {"liveChatContinuation": ["invalid"]}}]
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *args, **kwargs: next(responses),
    )
    monkeypatch.setattr(
        provider,
        "_get_initial_video_info",
        lambda *args: (
            {"continuation_info": {"Live chat": "replacement"}},
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    loop = _loop(provider, status=status)
    try:
        with pytest.raises(IncompleteContinuationError):
            list(loop.run())
        kind = "replay" if status == "was_live" else "live"
        assert loop.diagnostics[f"initial_{kind}_recovery_attempt_count"] == 1
        assert loop.diagnostics.get(f"initial_{kind}_recovery_success_count", 0) == 0
    finally:
        provider.close()


@pytest.mark.parametrize("status", ["was_live", "live"])
@pytest.mark.parametrize("view", ["live", "top"])
def test_initial_recovery_never_substitutes_another_chat_view(
    monkeypatch, status, view
):
    provider = YouTubeChatDownloader(request_profile="youtube_web")
    post = Mock(return_value=http_response(status_code=400, payload=_error()))
    monkeypatch.setattr(provider, "_session_post", post)
    other_view = "Live chat" if view == "top" else "Top chat"
    monkeypatch.setattr(
        provider,
        "_get_initial_video_info",
        lambda *_: (
            {"continuation_info": {other_view: "wrong-view"}},
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    loop = _loop(provider, status=status)
    loop.params = loop.params.with_updates(chat_type=view)
    loop.initial_info["continuation_info"]["Top chat"] = "first-top"
    try:
        with pytest.raises(NoContinuation, match=f"Initial {view} chat continuation"):
            list(loop.run())
    finally:
        provider.close()
    assert post.call_count == 1


@pytest.mark.parametrize(
    ("status", "replacement_status"),
    [("live", "was_live"), ("upcoming", "past"), ("was_live", "live")],
)
def test_initial_recovery_rejects_live_replay_mode_changes(
    monkeypatch, status, replacement_status
):
    provider = YouTubeChatDownloader(request_profile="youtube_web")
    post = Mock(return_value=http_response(status_code=400, payload=_error()))
    monkeypatch.setattr(provider, "_session_post", post)
    monkeypatch.setattr(
        provider,
        "_get_initial_video_info",
        lambda *_: (
            {
                "status": replacement_status,
                "continuation_info": {"Live chat": "replacement"},
            },
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    try:
        with pytest.raises(ChatDownloaderError, match="broadcast changed"):
            list(_loop(provider, status=status).run())
    finally:
        provider.close()
    assert post.call_count == 1


@pytest.mark.parametrize(
    ("code", "error_status", "video_id"),
    [
        (403, "INVALID_ARGUMENT", "fixture"),
        (400, "OTHER", "fixture"),
        (400, "INVALID_ARGUMENT", ""),
    ],
)
def test_initial_live_recovery_requires_exact_rejection_and_video_identity(
    monkeypatch, code, error_status, video_id
):
    provider = YouTubeChatDownloader(request_profile="youtube_web")
    error = _error()
    error["error"].update(code=code, status=error_status)
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *a, **kw: error,
    )
    refresh = Mock()
    monkeypatch.setattr(provider, "_get_initial_video_info", refresh)
    loop = _loop(provider, status="live")
    loop.initial_info["original_video_id"] = video_id
    try:
        with pytest.raises(ChatDownloaderError):
            list(loop.run())
        assert provider._request_profile == "youtube_web"
    finally:
        provider.close()
    refresh.assert_not_called()

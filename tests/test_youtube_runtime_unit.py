# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from chat_downloader.errors import (
    ChatDownloaderError,
    IncompleteContinuationError,
    InvalidParameter,
    NoChatReplay,
    NoContinuation,
)
from chat_downloader.models import ChatRequest
from chat_downloader.request_profiles import REQUEST_PROFILE_INNERTUBE_CONTEXTS
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.chat_users_retrieval import (
    YouTubeChatUsersRetrievalMixin,
    _copy_chat_metadata,
)
from chat_downloader.sites.youtube.continuation import (
    ContinuationLoopState,
    _advance_continuation_loop,
    _ContinuationLoop,
    _ContinuationProgress,
    _get_chat_messages,
    _resolve_poll_delay_ms,
    build_continuation_params,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
)


def _patch(monkeypatch, name, value):
    monkeypatch.setattr(f"chat_downloader.sites.youtube.continuation.{name}", value)


def _returns(monkeypatch, name, value):
    _patch(monkeypatch, name, lambda *_args, **_kwargs: value)


def _capture(monkeypatch, name="capture_debug_sample"):
    calls = []
    _patch(monkeypatch, name, lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


def _stub_loop(monkeypatch, *, msg_filter=None):
    _patch(
        monkeypatch,
        "_ContinuationLoop._build_context",
        lambda *_args, **_kwargs: _context(msg_filter=msg_filter),
    )
    _returns(monkeypatch, "build_continuation_params", {"continuation": "token"})


def _loop(downloader: object, *, ytcfg: dict | None = None) -> _ContinuationLoop:
    """Construct a loop bound to *downloader* for driving response-state methods."""
    return _ContinuationLoop(
        cast("Any", downloader), {}, ytcfg or {}, cast("Any", None)
    )


@pytest.fixture(autouse=True)
def _disable_youtube_poll_sleep(monkeypatch) -> None:
    _patch(monkeypatch, "polling_sleep", lambda _seconds: None)


def _patch_visitor_data(monkeypatch, return_value) -> None:
    _patch(monkeypatch, "extract_visitor_data", lambda _yt_info: return_value)


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = SimpleNamespace(headers={})
        self._session_post = object()
        self.invalid_type_checks: list[tuple[object, object]] = []
        self.header_updates: list[dict[str, str]] = []
        self.applied_profiles: list[str] = []
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def update_session_headers(self, headers) -> None:
        self.header_updates.append(headers)
        self.session.headers.update(headers)

    def replace_session_headers(self, headers, managed_names) -> None:
        for name in managed_names:
            self.session.headers.pop(name, None)
        self.update_session_headers(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        self.applied_profiles.append(profile_name)
        self._request_profile = profile_name
        return True


def _patch_request_context(monkeypatch):
    _returns(monkeypatch, "_generate_headers", {})
    _returns(monkeypatch, "_generate_sapisidhash_header", None)
    _returns(
        monkeypatch, "_get_innertube_context", {"client": {"visitorData": "visitor"}}
    )


def _messages(owner, initial_info, **params):
    return _get_chat_messages(
        owner,
        initial_info,
        {"INNERTUBE_API_KEY": "key"},
        ChatRequest(
            **{
                "url": "https://www.youtube.com/watch?v=abc",
                "chat_type": "live",
                "message_groups": ["messages"],
                **params,
            }
        ),
    )


def _video_info(status="live", *, top="top-token", live="live-token", **extra):
    return {
        "continuation_info": {"Top chat": top, "Live chat": live},
        "status": status,
        **extra,
    }


def _response(actions=(), *, continuations=None, **extra):
    chat = {"actions": list(actions)}
    if continuations is not None:
        chat["continuations"] = continuations
    return {"continuationContents": {"liveChatContinuation": chat}, **extra}


def _context(*, msg_filter=None):
    fields = {
        "continuation_url": "https://example.test/continuation",
        "innertube_context": {"client": {}},
        "msg_filter": msg_filter,
        "time_filter": None,
        "loop_state": ContinuationLoopState(continuation="token"),
        "live_start_time_ms": 0,
        "is_replay": False,
        "offset": None,
    }
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("visitor", "auth", "expected"),
    [
        (
            "visitor-1",
            "AUTH_TOKEN",
            {"authorization": "AUTH_TOKEN", "x-goog-visitor-id": "visitor-1"},
        ),
        (None, None, {}),
    ],
)
def test_handle_continuation_response_updates_headers(
    monkeypatch,
    visitor,
    auth,
    expected,
) -> None:
    _patch_visitor_data(monkeypatch, visitor)
    _returns(monkeypatch, "_generate_sapisidhash_header", auth)
    downloader = _DummyDownloader()

    _loop(downloader)._handle_continuation_response({"foo": "bar"}, {})

    assert downloader.session.headers == expected


@pytest.mark.parametrize("enabled", [False, True])
def test_successful_response_capture_opt_in_and_limit(monkeypatch, enabled):
    captured_samples = _capture(monkeypatch)
    if enabled:
        monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "yes")
    else:
        monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", raising=False)
    loop = _loop(object())
    for index in range(5):
        loop._capture_successful_response({"response": index})
    assert captured_samples == [
        (("youtube-continuation-response", {"response": index}), {"sample_limit": 3})
        for index in range(3 if enabled else 0)
    ]


def test_handle_continuation_response_logs_request_context(monkeypatch) -> None:
    """Logging includes click tracking, continuation token, and login info."""
    _returns(monkeypatch, "_generate_sapisidhash_header", None)
    logs: list[tuple[str, object]] = []
    _patch(monkeypatch, "log", lambda level, message: logs.append((level, message)))
    _loop(_DummyDownloader())._handle_continuation_response(
        {
            "responseContext": {
                "serviceTrackingParams": [{}, {"params": [{"value": "abc"}]}]
            }
        },
        {
            "context": {"clickTracking": {"clickTrackingParams": "ctp"}},
            "continuation": "next-token",
        },
    )
    assert len(logs) == 2
    assert all(level == "debug" for level, _message in logs)
    assert "Continuation parameters" in str(logs[0][1])
    assert "Session headers:" in str(logs[0][1])
    assert "Logged-in info:" in str(logs[1][1])


def _build_result(
    *,
    debug_info=None,
    timeout_ms=None,
    is_end=True,
    next_continuation=None,
):
    return SimpleNamespace(
        debug_info={} if debug_info is None else debug_info,
        timeout_ms=timeout_ms,
        is_end=is_end,
        next_continuation=next_continuation,
    )


def test_advance_continuation_loop_updates_state_without_sleep_at_end(
    monkeypatch,
) -> None:
    sleep_calls: list[float] = []
    logs: list[tuple[str, object]] = []
    end_page_calls: list[str] = []

    class RecordingTimeFilter:
        def end_page(self) -> None:
            end_page_calls.append("end")

    loop_state = ContinuationLoopState(continuation="token")
    ctx = SimpleNamespace(
        loop_state=loop_state,
        time_filter=RecordingTimeFilter(),
        is_replay=True,
        replay_poll_interval=None,
    )
    _patch(monkeypatch, "polling_sleep", sleep_calls.append)
    _patch(monkeypatch, "log", lambda level, message: logs.append((level, message)))

    terminal_response = _response(continuations=[])

    assert _advance_continuation_loop(ctx, terminal_response) is True
    assert ctx.loop_state is loop_state
    assert sleep_calls == []
    assert end_page_calls == []
    assert not any("Sleeping" in str(message) for _level, message in logs)


@pytest.mark.parametrize(
    ("is_replay", "interval", "timeout_ms", "delay"),
    [(True, 0.75, 5000, 0.75), (False, None, 100, 0.5), (False, None, None, 5.0)],
    ids=["explicit-replay-interval", "live-min-floor", "live-absent-timeout"],
)
def test_advance_continuation_loop_poll_delay(
    monkeypatch,
    is_replay,
    interval,
    timeout_ms,
    delay,
) -> None:
    sleep_calls = []
    ctx = SimpleNamespace(
        loop_state=ContinuationLoopState(continuation="token"),
        time_filter=None,
        is_replay=is_replay,
        replay_poll_interval=interval,
    )
    _patch(
        monkeypatch,
        "parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=timeout_ms, is_end=False),
    )
    _patch(monkeypatch, "update_state_from_result", lambda _state, _result: _state)
    _patch(monkeypatch, "polling_sleep", sleep_calls.append)
    _patch(monkeypatch, "log", lambda *_: None)

    assert _advance_continuation_loop(ctx, {}) is False
    assert sleep_calls == [delay]


@pytest.mark.parametrize(
    ("raw_delay_ms", "expected_delay_ms"),
    [
        (None, 5000),
        (True, 5000),
        ("soon", 5000),
        (-100, 5000),
        (0, 500),
        (100, 500),
        (5000, 5000),
        (8000, 8000),
        (10000, 8000),
        (20000, 8000),
    ],
)
def test_resolve_poll_delay_ms_clamps_and_falls_back(
    raw_delay_ms,
    expected_delay_ms,
) -> None:
    assert _resolve_poll_delay_ms(raw_delay_ms) == expected_delay_ms


def test_copy_chat_metadata_skips_chat_generator_and_private_attrs() -> None:
    chat_item = Chat(title="placeholder", id="placeholder")
    source = SimpleNamespace(chat=iter(()), title="real title", id="real-id")
    source._hidden = "secret"
    source.author = "example"
    source.status = "live"

    _copy_chat_metadata(chat_item, source)

    assert chat_item.title == "real title"
    assert chat_item.id == "real-id"
    assert vars(chat_item)["author"] == "example"
    assert "_hidden" not in vars(chat_item)
    assert chat_item.status == "live"


def test_chat_iteration_rejects_missing_initial_continuation() -> None:
    with pytest.raises(NoContinuation, match="Initial live chat continuation"):
        list(
            _get_chat_messages(
                _DummyDownloader(),
                {"continuation_info": {"Top chat": "only-one"}},
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(url="https://www.youtube.com/watch?v=abc"),
            ),
        )


@pytest.mark.parametrize(
    ("offset", "tracking", "expected_offset"),
    [(12345, "ctp", 7345), (2000, None, 0), (None, None, None)],
)
def test_build_continuation_params_live_offset(offset, tracking, expected_offset):
    params = build_continuation_params(
        {"client": {"visitorData": "visitor"}},
        ContinuationLoopState(
            continuation="live-token",
            click_tracking_params=tracking,
            offset_milliseconds=offset,
        ),
        is_replay=False,
    )
    expected = {
        "context": {"client": {"visitorData": "visitor"}},
        "continuation": "live-token",
    }
    if tracking is not None:
        expected["context"]["clickTracking"] = {"clickTrackingParams": tracking}
    if expected_offset is not None:
        expected["currentPlayerState"] = {"playerOffsetMs": expected_offset}
    assert params == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [({"timestamp": 6_000_000}, 5000), ({"timestamp": 500_000}, -500), ({}, None)],
    ids=["positive-live", "negative-backlog", "missing-timestamp"],
)
def test_derive_live_offset_milliseconds(message, expected) -> None:
    assert derive_live_offset_milliseconds(message, live_start_time_ms=1000) == expected


@pytest.mark.parametrize(
    ("message", "offset", "seconds", "text"),
    [
        ({"timestamp": 6_000_000}, 5000, 5.0, "0:05"),
        ({"timestamp": 500_000}, -1500, -1.5, "-0:01"),
        (
            {"timestamp": 6_000_000, "time_in_seconds": 7, "time_text": "0:07"},
            5000,
            7,
            "0:07",
        ),
    ],
    ids=["backfill", "signed-backlog", "preserve-existing"],
)
def test_enrich_live_message_timing(message, offset, seconds, text) -> None:
    enrich_live_message_timing(message, offset)

    assert message["time_in_seconds"] == seconds
    assert message["time_text"] == text


def test_enrich_live_message_timing_skips_when_live_offset_is_none() -> None:
    message = {"timestamp": 6_000_000}
    enrich_live_message_timing(message, None)

    assert "time_in_seconds" not in message
    assert "time_text" not in message


def test_get_chat_messages_by_user_args_stops_after_first_successful_chat(
    monkeypatch,
) -> None:
    class _StopPolling(Exception):
        pass

    class _DummyUserLookup(YouTubeChatUsersRetrievalMixin):
        def __init__(self) -> None:
            self.requested_video_ids: list[str] = []

        def get_user_videos(self, **_kwargs):
            return iter(
                [
                    _user_video("live-1", "Live One"),
                    _user_video("live-2", "Live Two"),
                ]
            )

        def get_chat_by_video_id(self, video_id, _params):
            self.requested_video_ids.append(video_id)
            return Chat(
                chat=iter([{"message": f"chat:{video_id}"}]),
                title=f"title:{video_id}",
                id=video_id,
            )

    monkeypatch.setattr(
        "chat_downloader.utils.timed_generator.polling_sleep",
        lambda _seconds: (_ for _ in ()).throw(_StopPolling()),
    )

    lookup = _DummyUserLookup()
    chat_item = Chat(title="placeholder", id="placeholder")
    params = ChatRequest(url="https://www.youtube.com/@example/live")
    generator = lookup._get_chat_messages_by_user_args({}, chat_item, params)

    assert next(generator) == {"message": "chat:live-1"}
    with pytest.raises(_StopPolling):
        next(generator)

    assert lookup.requested_video_ids == ["live-1"]
    assert chat_item.title == "title:live-1"
    assert chat_item.id == "live-1"


def test_chat_iteration_rejects_invalid_message_groups() -> None:
    with pytest.raises(InvalidParameter, match="Invalid groups specified"):
        list(
            _messages(
                _DummyDownloader(),
                _video_info(top="top", live="live"),
                message_groups=["not-a-group"],
            )
        )


@pytest.mark.parametrize(
    ("status", "chat_type", "error", "exception", "match"),
    [
        (
            "past",
            "live",
            {"code": 400, "message": "Replay disabled"},
            NoChatReplay,
            "Replay disabled",
        ),
        (
            "live",
            "top",
            {"code": 500, "message": "Server exploded"},
            ChatDownloaderError,
            "Server exploded",
        ),
        (
            "live",
            "live",
            "backend unavailable",
            ChatDownloaderError,
            "backend unavailable",
        ),
    ],
    ids=["replay-disabled", "non-replay-api-error", "non-dict-error"],
)
def test_chat_iteration_api_errors(
    monkeypatch,
    status,
    chat_type,
    error,
    exception,
    match,
) -> None:
    downloader = _DummyDownloader()
    captured_samples = _capture(monkeypatch)
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "1")
    _patch_request_context(monkeypatch)
    _returns(monkeypatch, "build_continuation_params", {"continuation": "token"})
    _returns(monkeypatch, "_get_continuation_info", {"error": error})

    with pytest.raises(exception, match=match):
        list(
            _messages(
                downloader,
                _video_info(status, top="top", live="live"),
                chat_type=chat_type,
            )
        )

    assert captured_samples == []


def test_chat_iteration_updates_headers_and_handles_no_actions(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    debug_messages = []
    captured_samples = []
    sleep_calls = []
    payload_summary = {
        "top_level_keys": ["continuationContents"],
        "continuation_contents_keys": ["liveChatContinuation"],
        "live_chat_keys": ["actions", "continuations"],
        "actions_count": 0,
        "continuation_keys": ["heartbeat"],
    }
    debug_info = {
        "continuation_key": "heartbeat",
        "continuation_entry": {"timeoutMs": 250},
        "payload_summary": payload_summary,
    }

    _returns(monkeypatch, "_generate_headers", {"x-bootstrap": "1"})
    _returns(monkeypatch, "_generate_sapisidhash_header", "AUTH_TOKEN")
    _returns(
        monkeypatch, "_get_innertube_context", {"client": {"visitorData": "visitor"}}
    )
    _patch(
        monkeypatch,
        "check_for_invalid_types",
        lambda message_types, valid_types: downloader.invalid_type_checks.append(
            (message_types, valid_types)
        ),
    )
    _returns(
        monkeypatch,
        "build_continuation_params",
        {"context": {}, "continuation": "live-token"},
    )
    _returns(monkeypatch, "_get_continuation_info", _response([], responseContext={}))
    _patch_visitor_data(monkeypatch, "visitor-2")
    _patch(
        monkeypatch,
        "parse_continuation_response",
        lambda _yt_info: _build_result(
            debug_info={"unknown": True, **debug_info},
            timeout_ms=250,
            is_end=True,
        ),
    )
    _patch(monkeypatch, "debug_log", lambda *items: debug_messages.append(items))
    _patch(
        monkeypatch,
        "capture_debug_sample",
        lambda label, payload, **kwargs: captured_samples.append(
            (label, payload, kwargs)
        ),
    )
    _patch(monkeypatch, "polling_sleep", sleep_calls.append)

    result = list(_messages(downloader, _video_info()))

    assert result == []
    assert downloader.invalid_type_checks
    assert downloader.session.headers["authorization"] == "AUTH_TOKEN"
    assert downloader.session.headers["x-goog-visitor-id"] == "visitor-2"
    assert sleep_calls == []
    assert captured_samples == [
        (
            "youtube-unknown-continuation-heartbeat",
            debug_info,
            {"sample_limit": 10},
        ),
    ]
    assert debug_messages == [
        (
            "Unknown continuation: heartbeat",
            {"heartbeat": {"timeoutMs": 250}},
            {"payload_summary": payload_summary},
        ),
    ]


def test_chat_iteration_reraises_incomplete_continuation_when_fallback_unavailable(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    original_error = IncompleteContinuationError("original")

    _stub_loop(monkeypatch)
    _patch(
        monkeypatch,
        "_get_continuation_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(original_error),
    )
    _returns(monkeypatch, "_ContinuationLoop._attempt_profile_fallback", False)

    with pytest.raises(IncompleteContinuationError, match="original"):
        list(_messages(downloader, _video_info(live="token")))


def test_chat_iteration_raises_when_live_chat_continuation_is_missing(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    captured_samples = _capture(monkeypatch)

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "1")

    _stub_loop(monkeypatch)
    _returns(monkeypatch, "_get_continuation_info", {"responseContext": {}})
    _returns(monkeypatch, "_ContinuationLoop._handle_continuation_response", None)
    _patch(monkeypatch, "summarize_continuation_payload", lambda _yt_info: "summary")

    with pytest.raises(IncompleteContinuationError, match="Summary: summary"):
        list(_messages(downloader, _video_info(live="token")))

    assert captured_samples == []


def test_chat_iteration_returns_immediately_when_action_processing_requests_stop(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    _stub_loop(monkeypatch)
    _returns(monkeypatch, "_get_continuation_info", _response([{"id": 1}]))
    _returns(monkeypatch, "_ContinuationLoop._handle_continuation_response", None)

    def _stop_processing(*_args, **_kwargs):
        if False:
            yield {}
        return True

    _patch(monkeypatch, "_process_actions", _stop_processing)
    _patch(
        monkeypatch,
        "_advance_continuation_loop",
        lambda *_args, **_kwargs: pytest.fail(
            "_advance_continuation_loop should not run after stop"
        ),
    )

    assert list(_messages(downloader, _video_info(live="token"))) == []


def test_chat_iteration_yields_chat_ended_when_clean_live_end(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    msg_filter = SimpleNamespace(should_add=lambda _message: True)
    captured_samples = _capture(monkeypatch)
    log_calls = []
    response = _response([])

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "on")

    _stub_loop(monkeypatch, msg_filter=msg_filter)
    _returns(monkeypatch, "_get_continuation_info", response)
    _returns(monkeypatch, "_ContinuationLoop._handle_continuation_response", None)
    _returns(monkeypatch, "_advance_continuation_loop", True)
    _patch(monkeypatch, "log", lambda *args: log_calls.append(args))

    assert list(_messages(downloader, _video_info(live="token"))) == [
        {
            "message_type": "chat_ended",
            "action_type": "chat_ended",
            "message": None,
        },
    ]
    assert captured_samples == [
        (
            ("youtube-continuation-response", response),
            {"sample_limit": 3},
        )
    ]
    assert log_calls == [
        (
            "debug",
            (
                "Processed actions in poll: 0; emitted messages: 0; "
                "non-emitted actions: 0"
            ),
        )
    ]


def test_chat_iteration_switches_profile_after_incomplete_continuation(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    call_state = {"count": 0}
    generated_header_configs = []

    def _record_headers(ytcfg, *_args, **_kwargs):
        generated_header_configs.append(ytcfg)
        return {}

    _patch(monkeypatch, "_generate_headers", _record_headers)
    _returns(monkeypatch, "_generate_sapisidhash_header", None)
    _returns(
        monkeypatch, "_get_innertube_context", {"client": {"visitorData": "visitor"}}
    )

    def fake_continuation(_url, _session_post, _params, **_kwargs):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise IncompleteContinuationError("incomplete")
        return _response([], responseContext={})

    _patch(monkeypatch, "_get_continuation_info", fake_continuation)
    _patch(
        monkeypatch,
        "parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=None, is_end=True),
    )

    list(
        _messages(
            downloader,
            _video_info(top="top", live="live"),
        ),
    )

    assert downloader.applied_profiles == ["youtube_android"]
    assert [
        config["INNERTUBE_CONTEXT_CLIENT_NAME"] for config in generated_header_configs
    ] == [1, 3]
    assert (
        generated_header_configs[-1]["INNERTUBE_CONTEXT"]["client"]["clientName"]
        == "ANDROID"
    )


def test_chat_iteration_live_updates_offset_from_message_timestamps(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    continuation_payloads = []
    responses = iter(
        [
            _response([{"id": 1}], responseContext={}),
            _response([], responseContext={}),
        ],
    )
    parse_results = iter(
        [
            _build_result(is_end=False, next_continuation="next-live"),
            _build_result(is_end=True, next_continuation=None),
        ],
    )

    _patch_request_context(monkeypatch)
    _patch(monkeypatch, "get_live_start_time_ms", lambda: 1000)

    def fake_get_continuation_info(_url, _session_post, _request, **kwargs):
        continuation_payloads.append(kwargs["json"])
        return next(responses)

    _patch(monkeypatch, "_get_continuation_info", fake_get_continuation_info)
    _returns(
        monkeypatch,
        "process_pipeline_action",
        PipelineResult(
            disposition="yield",
            message={"timestamp": 6_000_000},
        ),
    )
    _patch(
        monkeypatch, "parse_continuation_response", lambda _yt_info: next(parse_results)
    )

    messages = list(_messages(downloader, _video_info()))

    assert messages == [
        {"timestamp": 6_000_000, "time_in_seconds": 5.0, "time_text": "0:05"},
    ]
    expected_context = {
        "client": {
            "visitorData": "visitor",
            "clientName": "WEB",
            "clientVersion": REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_web"][
                "client"
            ]["clientVersion"],
            "hl": "en",
            "timeZone": "UTC",
            "utcOffsetMinutes": 0,
        }
    }
    assert continuation_payloads == [
        {"context": expected_context, "continuation": "live-token"},
        {
            "context": expected_context,
            "continuation": "next-live",
            "currentPlayerState": {"playerOffsetMs": 0},
        },
    ]


def test_chat_iteration_replay_processes_actions_and_ends_page(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    process_calls = []
    end_page_calls = []
    continuation_requests = []
    sleep_calls = []

    class FakeTimeFilter:
        def end_page(self) -> None:
            end_page_calls.append("end")

    responses = iter(
        [
            _response(
                actions,
                continuations=[
                    {
                        "liveChatReplayContinuationData": {
                            "continuation": token,
                            "timeUntilLastMessageMsec": 5000,
                        },
                    }
                ],
                responseContext={},
            )
            for actions, token in [
                ([{"id": 1}, {"id": 2}], "first-token"),
                ([], "last-token"),
            ]
        ]
        + [_response(continuations=[], responseContext={})]
    )

    _patch_request_context(monkeypatch)
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation_helpers.TimeRangeFilter",
        lambda *args, **kwargs: FakeTimeFilter(),
    )

    def fake_build_continuation_params(_context, state, *, is_replay):
        continuation_requests.append(state.continuation)
        return {"continuation": state.continuation}

    _patch(monkeypatch, "build_continuation_params", fake_build_continuation_params)
    _patch(
        monkeypatch, "_get_continuation_info", lambda *_args, **_kwargs: next(responses)
    )
    _patch(monkeypatch, "polling_sleep", sleep_calls.append)

    pipeline_results = iter(
        [
            PipelineResult(
                disposition="skip",
                non_emission_reason=NonEmissionReason.TIME_RANGE_FILTERED,
            ),
            PipelineResult(
                disposition="yield",
                message={"message": "hi"},
            ),
        ],
    )

    def fake_process_pipeline_action(
        action, offset, _msg_filter, _time_filter, _paid_events
    ):
        process_calls.append((action, offset))
        return next(pipeline_results)

    _patch(monkeypatch, "process_pipeline_action", fake_process_pipeline_action)
    messages = list(
        _messages(
            downloader,
            _video_info("past", offset=4.0),
            start_time=2,
            youtube_replay_poll_interval=0.75,
        ),
    )

    assert messages == [{"message": "hi"}]
    assert process_calls == [({"id": 1}, 4.0), ({"id": 2}, 4.0)]
    assert continuation_requests == ["live-token", "first-token", "last-token"]
    assert sleep_calls == [0.75, 0.75]
    assert end_page_calls == ["end", "end"]


def _user_video(video_id, title="Target", video_type="LIVE"):
    return {"video_id": video_id, "title": title, "video_type": video_type}


def test_replay_progress_rejects_repeated_stale_action_pages() -> None:
    progress = _ContinuationProgress(
        max_no_progress_polls=2,
        max_profile_fallbacks=1,
    )
    for offset, advanced, exhausted in [
        ("1797798", True, False),
        ("1797798", False, False),
        ("1797798", False, True),
        ("1800332", True, False),
    ]:
        assert (
            progress.response_advanced(
                [{"replayChatItemAction": {"videoOffsetTimeMsec": offset}}],
                token_changed=False,
                is_replay=True,
            )
            is advanced
        )
        assert progress.register_poll(made_progress=advanced) is exhausted
    assert progress.no_progress_count == 0


@pytest.mark.parametrize("raw_offset", ["", "nan", "-1"])
def test_replay_progress_ignores_invalid_offsets(raw_offset: str) -> None:
    progress = _ContinuationProgress(
        max_no_progress_polls=1,
        max_profile_fallbacks=1,
    )

    assert not progress.response_advanced(
        [{"replayChatItemAction": {"videoOffsetTimeMsec": raw_offset}}],
        token_changed=False,
        is_replay=True,
    )


def test_user_chat_lookup_skips_ignored_and_non_live_videos_before_yield() -> None:
    def make_chat(video_id):
        chat = Chat(chat=iter([{"message": "hello"}]), title="Target", id=video_id)
        chat.author = "Uploader"
        chat._private = "ignored"
        return chat

    class DummyUsers(YouTubeChatUsersRetrievalMixin):
        def __init__(self) -> None:
            self._videos = iter(
                [
                    _user_video("old", "Old", "VOD"),
                    _user_video("skip-me", "Ignored"),
                    _user_video("keep-me", video_type="UPCOMING"),
                ]
            )

        def get_user_videos(self, **_kwargs):
            return self._videos

        def get_chat_by_video_id(self, video_id, params):
            assert video_id == "keep-me"
            assert params.ignore == ["skip-me"]
            return make_chat(video_id)

    request = ChatRequest(
        url="https://www.youtube.com/@example/live",
        ignore=["skip-me"],
    )
    downloader = DummyUsers()

    chat_item = downloader._get_chat_by_user_args({"handle": "example"}, request)

    assert next(chat_item.chat) == {"message": "hello"}
    assert chat_item.title == "Target"
    assert chat_item.id == "keep-me"
    assert vars(chat_item)["author"] == "Uploader"
    assert "_private" not in vars(chat_item)


def test_user_chat_lookup_retries_after_chat_errors(monkeypatch) -> None:
    class RetrySentinel(Exception):
        pass

    class DummyUsers(YouTubeChatUsersRetrievalMixin):
        def get_user_videos(self, **_kwargs):
            return iter([_user_video("keep-me")])

        def get_chat_by_video_id(self, video_id, params) -> NoReturn:
            msg = f"boom for {video_id} / {params.url}"
            raise ChatDownloaderError(msg)

    monkeypatch.setattr(
        "chat_downloader.utils.timed_generator.polling_sleep",
        lambda _seconds: (_ for _ in ()).throw(RetrySentinel()),
    )

    downloader = DummyUsers()
    generator = downloader._get_chat_messages_by_user_args(
        {"handle": "example"},
        Chat(title="placeholder", id="placeholder"),
        ChatRequest(url="https://www.youtube.com/@example/live"),
    )

    with pytest.raises(RetrySentinel):
        next(generator)

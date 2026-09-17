# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

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
    _resolve_poll_delay_ms,
    build_continuation_params,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
)
from tests.youtube_third_helpers import (
    Downloader as _DummyDownloader,
)
from tests.youtube_third_helpers import (
    response as _response,
)
from tests.youtube_third_helpers import (
    video_info as _video_info,
)


def _patch(monkeypatch, name, value):
    monkeypatch.setattr(f"chat_downloader.sites.youtube.continuation.{name}", value)
    return value


def _returns(monkeypatch, name, value):
    return _patch(monkeypatch, name, Mock(return_value=value))


def _build_result(
    *,
    debug_info=None,
    timeout_ms=None,
    is_end=True,
    next_continuation=None,
    click_tracking_params=None,
):
    return SimpleNamespace(
        debug_info={} if debug_info is None else debug_info,
        timeout_ms=timeout_ms,
        is_end=is_end,
        next_continuation=next_continuation,
        click_tracking_params=click_tracking_params,
    )


def _context(**overrides):
    return SimpleNamespace(
        **{
            "continuation_url": "https://example.test/continuation",
            "innertube_context": {"client": {}},
            "msg_filter": None,
            "time_filter": None,
            "loop_state": ContinuationLoopState(continuation="token"),
            "live_start_time_ms": 0,
            "is_replay": False,
            "offset": None,
            "replay_poll_interval": None,
            **overrides,
        }
    )


def _loop(downloader):
    return _ContinuationLoop(cast("Any", downloader), {}, {}, cast("Any", None))


def _messages(owner, initial_info, **params):
    request = ChatRequest(
        **{
            "url": "https://www.youtube.com/watch?v=abc",
            "chat_type": "live",
            "message_groups": ["messages"],
            **params,
        }
    )
    return _ContinuationLoop(
        owner, initial_info, {"INNERTUBE_API_KEY": "key"}, request
    ).run()


@pytest.fixture(autouse=True)
def _disable_youtube_poll_sleep(monkeypatch):
    """Replace polling sleep with a mock and return it for call assertions."""
    return _patch(monkeypatch, "polling_sleep", Mock())


@pytest.fixture
def polling(monkeypatch, _disable_youtube_poll_sleep):
    """Configure transport without replacing the continuation state machine."""
    _returns(monkeypatch, "_generate_headers", {})
    _returns(monkeypatch, "_generate_sapisidhash_header", None)
    _returns(
        monkeypatch, "_get_innertube_context", {"client": {"visitorData": "visitor"}}
    )
    monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", raising=False)
    return SimpleNamespace(
        downloader=_DummyDownloader(),
        fetch=_patch(monkeypatch, "_get_continuation_info", Mock()),
        capture=_patch(monkeypatch, "capture_debug_sample", Mock()),
        sleep=_disable_youtube_poll_sleep,
    )


def _stub_loop(monkeypatch, *, msg_filter=None):
    _returns(
        monkeypatch, "_ContinuationLoop._build_context", _context(msg_filter=msg_filter)
    )
    _returns(monkeypatch, "build_continuation_params", {"continuation": "token"})
    _returns(monkeypatch, "_ContinuationLoop._handle_continuation_response", None)


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
def test_handle_continuation_response_headers_and_request_context(
    monkeypatch, visitor, auth, expected
):
    _returns(monkeypatch, "extract_visitor_data", visitor)
    _returns(monkeypatch, "_generate_sapisidhash_header", auth)
    downloader = _DummyDownloader()
    _loop(downloader)._handle_continuation_response(
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
    assert downloader.session.headers == expected


@pytest.mark.parametrize("enabled", [False, True])
def test_successful_response_capture_opt_in_and_limit(monkeypatch, enabled):
    captured = _patch(monkeypatch, "capture_debug_sample", Mock())
    if enabled:
        monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "yes")
    else:
        monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", raising=False)
    loop = _loop(object())
    for index in range(5):
        loop._capture_successful_response({"response": index})
    assert captured.call_args_list == [
        call("youtube-continuation-response", {"response": index}, sample_limit=3)
        for index in range(3 if enabled else 0)
    ]


@pytest.mark.parametrize(
    ("terminal", "is_replay", "interval", "timeout_ms", "delay"),
    [
        (True, True, None, None, None),
        (False, True, 0.75, 5000, 0.75),
        (False, False, None, 100, 0.5),
        (False, False, None, None, 5.0),
    ],
    ids=[
        "terminal-no-sleep",
        "explicit-replay-interval",
        "live-min-floor",
        "live-absent-timeout",
    ],
)
@pytest.mark.usefixtures("_disable_youtube_poll_sleep")
def test_advance_continuation_loop(
    _disable_youtube_poll_sleep,  # noqa: PT019 - value asserted below
    monkeypatch,
    terminal,
    is_replay,
    interval,
    timeout_ms,
    delay,
):
    time_filter = Mock() if terminal else None
    ctx = _context(
        time_filter=time_filter, is_replay=is_replay, replay_poll_interval=interval
    )
    state = ctx.loop_state
    if not terminal:
        _returns(
            monkeypatch,
            "parse_continuation_response",
            _build_result(timeout_ms=timeout_ms, is_end=False),
        )
        _patch(monkeypatch, "update_state_from_result", lambda state, _result: state)
    assert _advance_continuation_loop(ctx, _response(continuations=[])) is terminal
    assert ctx.loop_state is state
    assert _disable_youtube_poll_sleep.call_args_list == (
        [] if terminal else [call(delay)]
    )
    if terminal:
        time_filter.end_page.assert_not_called()


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
def test_resolve_poll_delay_ms_clamps_and_falls_back(raw_delay_ms, expected_delay_ms):
    assert _resolve_poll_delay_ms(raw_delay_ms) == expected_delay_ms


def test_copy_chat_metadata_skips_chat_generator_and_private_attrs():
    chat_item = Chat(title="placeholder", id="placeholder")
    original_generator = chat_item.chat
    source = SimpleNamespace(
        chat=iter(()),
        title="real title",
        id="real-id",
        _hidden="secret",
        author="example",
        status="live",
    )
    _copy_chat_metadata(chat_item, source)
    assert (chat_item.title, chat_item.id, chat_item.author, chat_item.status) == (
        "real title",
        "real-id",
        "example",
        "live",
    )
    assert "_hidden" not in vars(chat_item)
    assert chat_item.chat is original_generator


@pytest.mark.parametrize(
    ("initial_info", "params", "exception"),
    [
        ({"continuation_info": {"Top chat": "only-one"}}, {}, NoContinuation),
        (
            _video_info(top="top", live="live"),
            {"message_groups": ["not-a-group"]},
            InvalidParameter,
        ),
    ],
)
def test_chat_iteration_rejects_invalid_initial_request(
    initial_info, params, exception
):
    with pytest.raises(exception):
        list(_messages(_DummyDownloader(), initial_info, **params))


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
def test_derive_live_offset_milliseconds(message, expected):
    assert derive_live_offset_milliseconds(message, live_start_time_ms=1000) == expected


@pytest.mark.parametrize(
    ("message", "offset", "timing"),
    [
        ({"timestamp": 6_000_000}, 5000, {"time_in_seconds": 5.0, "time_text": "0:05"}),
        (
            {"timestamp": 500_000},
            -1500,
            {"time_in_seconds": -1.5, "time_text": "-0:01"},
        ),
        (
            {"timestamp": 6_000_000, "time_in_seconds": 7, "time_text": "0:07"},
            5000,
            {"time_in_seconds": 7, "time_text": "0:07"},
        ),
        ({"timestamp": 6_000_000}, None, {}),
    ],
    ids=["backfill", "signed-backlog", "preserve-existing", "missing-offset"],
)
def test_enrich_live_message_timing(message, offset, timing):
    expected = {**message, **timing}
    enrich_live_message_timing(message, offset)
    assert message == expected


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
    monkeypatch, polling, status, chat_type, error, exception, match
):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "1")
    _returns(monkeypatch, "build_continuation_params", {"continuation": "token"})
    polling.fetch.return_value = {"error": error}
    with pytest.raises(exception, match=match):
        list(
            _messages(
                polling.downloader,
                _video_info(status, top="top", live="live"),
                chat_type=chat_type,
            )
        )
    polling.capture.assert_not_called()


def test_chat_iteration_updates_headers_and_handles_no_actions(monkeypatch, polling):
    debug_info = {
        "continuation_key": "heartbeat",
        "continuation_entry": {"timeoutMs": 250},
        "payload_summary": {
            "top_level_keys": ["continuationContents"],
            "continuation_contents_keys": ["liveChatContinuation"],
            "live_chat_keys": ["actions", "continuations"],
            "actions_count": 0,
            "continuation_keys": ["heartbeat"],
        },
    }
    _returns(monkeypatch, "_generate_headers", {"x-bootstrap": "1"})
    _returns(monkeypatch, "_generate_sapisidhash_header", "AUTH_TOKEN")
    invalid_types = _patch(monkeypatch, "check_for_invalid_types", Mock())
    _returns(
        monkeypatch,
        "build_continuation_params",
        {"context": {}, "continuation": "live-token"},
    )
    polling.fetch.return_value = _response([], responseContext={})
    _returns(monkeypatch, "extract_visitor_data", "visitor-2")
    _returns(
        monkeypatch,
        "parse_continuation_response",
        _build_result(debug_info={"unknown": True, **debug_info}, timeout_ms=250),
    )
    debug = _patch(monkeypatch, "debug_log", Mock())
    assert list(_messages(polling.downloader, _video_info())) == []
    invalid_types.assert_called_once()
    assert polling.downloader.session.headers["authorization"] == "AUTH_TOKEN"
    assert polling.downloader.session.headers["x-goog-visitor-id"] == "visitor-2"
    polling.sleep.assert_not_called()
    polling.capture.assert_called_once_with(
        "youtube-unknown-continuation-heartbeat", debug_info, sample_limit=10
    )
    assert [entry.args[1:] for entry in debug.call_args_list] == [
        (
            {"heartbeat": {"timeoutMs": 250}},
            {"payload_summary": debug_info["payload_summary"]},
        )
    ]


@pytest.mark.parametrize("missing_body", [False, True])
def test_chat_iteration_incomplete_response(monkeypatch, polling, missing_body):
    _stub_loop(monkeypatch)
    original_error = IncompleteContinuationError("original")
    if missing_body:
        monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "1")
        polling.fetch.return_value = {"responseContext": {}}
        _returns(monkeypatch, "summarize_continuation_payload", "summary")
    else:
        polling.fetch.side_effect = original_error
        _returns(monkeypatch, "_ContinuationLoop._attempt_profile_fallback", False)
    with pytest.raises(IncompleteContinuationError) as raised:
        list(_messages(polling.downloader, _video_info(live="token")))
    if not missing_body:
        assert raised.value is original_error
    polling.capture.assert_not_called()


@pytest.mark.parametrize("stop_requested", [False, True])
def test_chat_iteration_terminal_action_lifecycle(monkeypatch, polling, stop_requested):
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES", "on")
    _stub_loop(
        monkeypatch, msg_filter=SimpleNamespace(should_add=lambda _message: True)
    )
    response = _response([{"id": 1}] if stop_requested else [])
    polling.fetch.return_value = response
    advance = _returns(monkeypatch, "_advance_continuation_loop", True)
    if stop_requested:

        def stop_processing(*_args, **_kwargs):
            yield from ()
            return True

        _patch(monkeypatch, "_process_actions", stop_processing)
    expected = (
        []
        if stop_requested
        else [
            {"message_type": "chat_ended", "action_type": "chat_ended", "message": None}
        ]
    )
    assert list(_messages(polling.downloader, _video_info(live="token"))) == expected
    assert advance.call_count == (0 if stop_requested else 1)
    polling.capture.assert_called_once_with(
        "youtube-continuation-response", response, sample_limit=3
    )


def test_chat_iteration_switches_profile_after_incomplete_continuation(
    monkeypatch, polling
):
    headers = _returns(monkeypatch, "_generate_headers", {})
    polling.fetch.side_effect = [
        IncompleteContinuationError("incomplete"),
        _response([], responseContext={}),
    ]
    _returns(monkeypatch, "parse_continuation_response", _build_result())
    list(_messages(polling.downloader, _video_info(top="top", live="live")))
    assert polling.downloader.applied_profiles == ["youtube_android"]
    configs = [entry.args[0] for entry in headers.call_args_list]
    assert [config["INNERTUBE_CONTEXT_CLIENT_NAME"] for config in configs] == [1, 3]
    assert configs[-1]["INNERTUBE_CONTEXT"]["client"]["clientName"] == "ANDROID"


def test_chat_iteration_live_updates_offset_from_message_timestamps(
    monkeypatch, polling
):
    polling.fetch.side_effect = [
        _response([{"id": 1}], responseContext={}),
        _response([], responseContext={}),
    ]
    _returns(monkeypatch, "get_live_start_time_ms", 1000)
    _returns(
        monkeypatch,
        "process_pipeline_action",
        PipelineResult(disposition="yield", message={"timestamp": 6_000_000}),
    )
    _patch(
        monkeypatch,
        "parse_continuation_response",
        Mock(
            side_effect=[
                _build_result(is_end=False, next_continuation="next-live"),
                _build_result(),
            ]
        ),
    )
    assert list(_messages(polling.downloader, _video_info())) == [
        {"timestamp": 6_000_000, "time_in_seconds": 5.0, "time_text": "0:05"}
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
    assert [entry.kwargs["json"] for entry in polling.fetch.call_args_list] == [
        {"context": expected_context, "continuation": "live-token"},
        {
            "context": expected_context,
            "continuation": "next-live",
            "currentPlayerState": {"playerOffsetMs": 0},
        },
    ]


def test_chat_iteration_replay_processes_actions_and_ends_page(monkeypatch, polling):
    time_filter = Mock()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation_helpers.TimeRangeFilter",
        Mock(return_value=time_filter),
    )
    polling.fetch.side_effect = [
        _response(
            actions,
            continuations=[
                {
                    "liveChatReplayContinuationData": {
                        "continuation": token,
                        "timeUntilLastMessageMsec": 5000,
                    }
                }
            ],
            responseContext={},
        )
        for actions, token in [
            ([{"id": 1}, {"id": 2}], "first-token"),
            ([], "last-token"),
        ]
    ] + [_response(continuations=[], responseContext={})]
    process = _patch(
        monkeypatch,
        "process_pipeline_action",
        Mock(
            side_effect=[
                PipelineResult(
                    disposition="skip",
                    non_emission_reason=NonEmissionReason.TIME_RANGE_FILTERED,
                ),
                PipelineResult(disposition="yield", message={"message": "hi"}),
            ]
        ),
    )
    assert list(
        _messages(
            polling.downloader,
            _video_info("past", offset=4.0),
            start_time=2,
            youtube_replay_poll_interval=0.75,
        )
    ) == [{"message": "hi"}]
    assert [entry.args[:2] for entry in process.call_args_list] == [
        ({"id": 1}, 4.0),
        ({"id": 2}, 4.0),
    ]
    assert [
        entry.kwargs["json"]["continuation"] for entry in polling.fetch.call_args_list
    ] == ["live-token", "first-token", "last-token"]
    assert polling.sleep.call_args_list == [call(0.75), call(0.75)]
    assert time_filter.end_page.call_args_list == [call(), call()]


def test_replay_progress_rejects_repeated_stale_action_pages():
    progress = _ContinuationProgress(max_no_progress_polls=2, max_profile_fallbacks=1)
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
def test_replay_progress_ignores_invalid_offsets(raw_offset):
    progress = _ContinuationProgress(max_no_progress_polls=1, max_profile_fallbacks=1)
    assert not progress.response_advanced(
        [{"replayChatItemAction": {"videoOffsetTimeMsec": raw_offset}}],
        token_changed=False,
        is_replay=True,
    )


def _user_video(video_id, title="Target", video_type="LIVE"):
    return {"video_id": video_id, "title": title, "video_type": video_type}


class _UserLookup(YouTubeChatUsersRetrievalMixin):
    def __init__(self, videos, make_chat):
        self.get_user_videos = Mock(side_effect=lambda **_kwargs: iter(videos))
        self.get_chat_by_video_id = Mock(side_effect=make_chat)


class _StopPolling(Exception):
    pass


@pytest.mark.parametrize("scenario", ["first-success", "skip-ignored", "retry-error"])
def test_user_chat_lookup_lifecycle(monkeypatch, scenario):
    def make_chat(video_id, params):
        if scenario == "retry-error":
            raise ChatDownloaderError(f"boom for {video_id} / {params.url}")
        if scenario == "skip-ignored":
            assert params.ignore == ["skip-me"]
            chat = Chat(chat=iter([{"message": "hello"}]), title="Target", id=video_id)
            chat.author = "Uploader"
            chat._private = "ignored"
            return chat
        return Chat(
            chat=iter([{"message": f"chat:{video_id}"}]),
            title=f"title:{video_id}",
            id=video_id,
        )

    videos = {
        "first-success": [
            _user_video("live-1", "Live One"),
            _user_video("live-2", "Live Two"),
        ],
        "skip-ignored": [
            _user_video("old", "Old", "VOD"),
            _user_video("skip-me", "Ignored"),
            _user_video("keep-me", video_type="UPCOMING"),
        ],
        "retry-error": [_user_video("keep-me")],
    }[scenario]
    monkeypatch.setattr(
        "chat_downloader.utils.timed_generator.polling_sleep",
        Mock(side_effect=_StopPolling()),
    )
    lookup = _UserLookup(videos, make_chat)
    request = ChatRequest(
        url="https://www.youtube.com/@example/live",
        ignore=["skip-me"] if scenario == "skip-ignored" else None,
    )
    if scenario == "skip-ignored":
        chat_item = lookup._get_chat_by_user_args({"handle": "example"}, request)
        assert next(chat_item.chat) == {"message": "hello"}
        assert (chat_item.title, chat_item.id, chat_item.author) == (
            "Target",
            "keep-me",
            "Uploader",
        )
        assert "_private" not in vars(chat_item)
    else:
        chat_item = Chat(title="placeholder", id="placeholder")
        generator = lookup._get_chat_messages_by_user_args(
            {} if scenario == "first-success" else {"handle": "example"},
            chat_item,
            request,
        )
        if scenario == "first-success":
            assert next(generator) == {"message": "chat:live-1"}
            assert (chat_item.title, chat_item.id) == ("title:live-1", "live-1")
        with pytest.raises(_StopPolling):
            next(generator)
    assert [entry.args[0] for entry in lookup.get_chat_by_video_id.call_args_list] == [
        "live-1" if scenario == "first-success" else "keep-me"
    ]

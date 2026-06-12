# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import NoReturn

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
from chat_downloader.sites.youtube.chat_streams_response import (
    _apply_response_state_updates,
    _handle_continuation_response,
    _log_request_context,
)
from chat_downloader.sites.youtube.chat_streams_runtime_iteration import (
    _advance_continuation_loop,
    _get_chat_messages,
    _resolve_poll_delay_ms,
)
from chat_downloader.sites.youtube.chat_users_retrieval import (
    YouTubeChatUsersRetrievalMixin,
    _copy_chat_metadata,
)
from chat_downloader.sites.youtube.continuation_loop_runtime import (
    build_continuation_params,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
)
from chat_downloader.sites.youtube.continuation_loop_state import (
    ContinuationLoopState,
)


@pytest.fixture(autouse=True)
def _disable_youtube_poll_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        lambda _seconds: None,
    )


def _patch_visitor_data(monkeypatch, return_value) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response.extract_visitor_data",
        lambda _yt_info: return_value,
    )


class _DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = _DummySession()
        self._session_post = object()
        self.invalid_type_checks: list[tuple[object, object]] = []
        self.header_updates: list[dict[str, str]] = []
        self.applied_profiles: list[str] = []
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def check_for_invalid_types(self, message_types, valid_types) -> None:
        self.invalid_type_checks.append((message_types, valid_types))

    def update_session_headers(self, headers) -> None:
        self.header_updates.append(headers)
        self.session.headers.update(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        self.applied_profiles.append(profile_name)
        self._request_profile = profile_name
        return True


def test_apply_response_state_updates_records_authorization_and_visitor_headers(
    monkeypatch,
) -> None:
    """Authorization and visitor headers are written when both are supplied."""
    _patch_visitor_data(monkeypatch, "visitor-1")

    downloader = _DummyDownloader()
    _apply_response_state_updates(downloader, {"foo": "bar"}, "AUTH_TOKEN")

    assert downloader.session.headers["authorization"] == "AUTH_TOKEN"
    assert downloader.session.headers["x-goog-visitor-id"] == "visitor-1"


def test_apply_response_state_updates_skips_missing_visitor_when_not_present(
    monkeypatch,
) -> None:
    """Missing visitor payload does not add the x-goog-visitor-id header."""
    _patch_visitor_data(monkeypatch, None)

    downloader = _DummyDownloader()
    _apply_response_state_updates(downloader, {"foo": "bar"}, None)

    assert downloader.session.headers == {}


def test_handle_continuation_response_composes_state_log_and_error_checks(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: "AUTH",
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._apply_response_state_updates",
        lambda *_args, **_kwargs: calls.append("state"),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._log_request_context",
        lambda *_args, **_kwargs: calls.append("log"),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._raise_if_api_error",
        lambda *_args, **_kwargs: calls.append("error"),
    )

    _handle_continuation_response(
        object(),
        {"responseContext": {}},
        {"INNERTUBE_API_KEY": "key"},
        {"continuation": "next-token"},
    )

    assert calls == ["state", "log", "error"]


def test_log_request_context_includes_click_tracking_and_logged_in_info(
    monkeypatch,
) -> None:
    """Logging includes click tracking, continuation token, and login info."""
    logs: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response.log",
        lambda level, message: logs.append((level, message)),
    )

    _log_request_context(
        _DummyDownloader(),
        {
            "responseContext": {
                "serviceTrackingParams": [
                    {},
                    {"params": [{"value": "abc"}]},
                ],
            },
        },
        {
            "context": {"clickTracking": {"clickTrackingParams": "ctp"}},
            "continuation": "next-token",
        },
    )

    assert len(logs) == 2
    level_one, message_one = logs[0]
    level_two, message_two = logs[1]
    assert level_one == "debug"
    assert level_two == "debug"
    assert "Continuation parameters" in str(message_one)
    assert "Session headers:" in str(message_one)
    assert "Logged-in info:" in str(message_two)


def _build_result(
    *,
    debug_info=None,
    timeout_ms=None,
    is_end=True,
    next_continuation="next-token",
):
    return SimpleNamespace(
        debug_info={} if debug_info is None else debug_info,
        timeout_ms=timeout_ms,
        is_end=is_end,
        next_continuation=next_continuation,
    )


def test_advance_continuation_loop_updates_state_and_sleeps(
    monkeypatch,
) -> None:
    sleep_calls: list[float] = []
    logs: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        loop_state=ContinuationLoopState(continuation="token"),
        time_filter=None,
        is_replay=True,
    )
    next_state = ContinuationLoopState(continuation="next-token")

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=250, is_end=True),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.update_state_from_result",
        lambda _state, _result: next_state,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        sleep_calls.append,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.log",
        lambda level, message: logs.append((level, message)),
    )

    assert _advance_continuation_loop(ctx, {"responseContext": {}}) is True
    assert ctx.loop_state is next_state
    assert sleep_calls == [0.5]
    assert any("Sleeping for 500ms." in str(message) for _level, message in logs)


def test_advance_continuation_loop_applies_min_floor_for_live_stream(
    monkeypatch,
) -> None:
    sleep_calls: list[float] = []
    ctx = SimpleNamespace(
        loop_state=ContinuationLoopState(continuation="token"),
        time_filter=None,
        is_replay=False,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=100, is_end=False),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.update_state_from_result",
        lambda _state, _result: _state,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        sleep_calls.append,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.log",
        lambda *_: None,
    )

    assert _advance_continuation_loop(ctx, {}) is False
    assert sleep_calls == [0.5]


def test_advance_continuation_loop_applies_floor_when_timeout_absent_for_live(
    monkeypatch,
) -> None:
    sleep_calls: list[float] = []
    ctx = SimpleNamespace(
        loop_state=ContinuationLoopState(continuation="token"),
        time_filter=None,
        is_replay=False,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=None, is_end=False),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.update_state_from_result",
        lambda _state, _result: _state,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        sleep_calls.append,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.log",
        lambda *_: None,
    )

    assert _advance_continuation_loop(ctx, {}) is False
    assert sleep_calls == [5.0]


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

    _copy_chat_metadata(chat_item, source)

    assert chat_item.title == "real title"
    assert chat_item.id == "real-id"
    assert vars(chat_item)["author"] == "example"
    assert "_hidden" not in vars(chat_item)


def test_copy_chat_metadata() -> None:
    chat_item = Chat()
    source = Chat(title="Test Title", status="live")
    _copy_chat_metadata(chat_item, source)
    assert chat_item.title == "Test Title"
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


def test_build_continuation_params_includes_live_player_offset_when_available() -> None:
    params = build_continuation_params(
        {"client": {"visitorData": "visitor"}},
        ContinuationLoopState(
            continuation="live-token",
            click_tracking_params="ctp",
            offset_milliseconds=12345,
        ),
        is_replay=False,
    )

    assert params == {
        "context": {
            "client": {"visitorData": "visitor"},
            "clickTracking": {"clickTrackingParams": "ctp"},
        },
        "continuation": "live-token",
        "currentPlayerState": {"playerOffsetMs": 7345},
    }


def test_build_continuation_params_clamps_small_player_offset_to_zero() -> None:
    params = build_continuation_params(
        {"client": {"visitorData": "visitor"}},
        ContinuationLoopState(
            continuation="live-token",
            offset_milliseconds=2000,
        ),
        is_replay=False,
    )

    assert params["currentPlayerState"] == {"playerOffsetMs": 0}


def test_build_continuation_params_omits_player_offset_when_unavailable() -> None:
    params = build_continuation_params(
        {"client": {"visitorData": "visitor"}},
        ContinuationLoopState(continuation="live-token"),
        is_replay=False,
    )

    assert params == {
        "context": {"client": {"visitorData": "visitor"}},
        "continuation": "live-token",
    }


def test_derive_live_offset_milliseconds_returns_non_negative_value() -> None:
    message = {"timestamp": 6_000_000}
    assert derive_live_offset_milliseconds(message, live_start_time_ms=1000) == 5000


def test_derive_live_offset_milliseconds_returns_none_without_timestamp() -> None:
    assert derive_live_offset_milliseconds({}, live_start_time_ms=1000) is None


def test_enrich_live_message_timing_backfills_time_fields() -> None:
    message = {"timestamp": 6_000_000}
    enrich_live_message_timing(message, 5000)

    assert message["time_in_seconds"] == 5.0
    assert message["time_text"] == "0:05"


def test_enrich_live_message_timing_preserves_existing_time_fields() -> None:
    message = {
        "timestamp": 6_000_000,
        "time_in_seconds": 7,
        "time_text": "0:07",
    }
    enrich_live_message_timing(message, 5000)

    assert message["time_in_seconds"] == 7
    assert message["time_text"] == "0:07"


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
                    {
                        "video_id": "live-1",
                        "video_type": "LIVE",
                        "title": "Live One",
                    },
                    {
                        "video_id": "live-2",
                        "video_type": "LIVE",
                        "title": "Live Two",
                    },
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
            _get_chat_messages(
                _DummyDownloader(),
                {
                    "continuation_info": {
                        "Top chat": "top",
                        "Live chat": "live",
                    },
                    "status": "live",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    message_groups=["not-a-group"],
                ),
            ),
        )


def test_chat_iteration_raises_no_chat_replay_on_replay_api_400(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {
            "error": {"code": 400, "message": "Replay disabled"},
        },
    )

    with pytest.raises(NoChatReplay, match="Replay disabled"):
        list(
            _get_chat_messages(
                downloader,
                {
                    "continuation_info": {
                        "Top chat": "top",
                        "Live chat": "live",
                    },
                    "status": "past",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            ),
        )


def test_chat_iteration_raises_api_error_for_non_replay_failure(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {
            "error": {"code": 500, "message": "Server exploded"},
        },
    )

    with pytest.raises(ChatDownloaderError, match="Server exploded"):
        list(
            _get_chat_messages(
                downloader,
                {
                    "continuation_info": {
                        "Top chat": "top",
                        "Live chat": "live",
                    },
                    "status": "live",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="top",
                    message_groups=["messages"],
                ),
            ),
        )


def test_chat_iteration_raises_api_error_for_non_dict_error_payload(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {"error": "backend unavailable"},
    )

    with pytest.raises(ChatDownloaderError, match="backend unavailable"):
        list(
            _get_chat_messages(
                downloader,
                {
                    "continuation_info": {
                        "Top chat": "top",
                        "Live chat": "live",
                    },
                    "status": "live",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            ),
        )


def test_chat_iteration_updates_headers_and_handles_no_actions(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    debug_messages = []
    captured_samples = []
    sleep_calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {"x-bootstrap": "1"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: "AUTH_TOKEN",
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"context": {}, "continuation": "live-token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
            "responseContext": {},
        },
    )
    _patch_visitor_data(monkeypatch, "visitor-2")
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(
            debug_info={
                "unknown": True,
                "continuation_key": "heartbeat",
                "continuation_entry": {"timeoutMs": 250},
                "payload_summary": {
                    "top_level_keys": ["continuationContents"],
                    "continuation_contents_keys": ["liveChatContinuation"],
                    "live_chat_keys": ["actions", "continuations"],
                    "actions_count": 0,
                    "continuation_keys": ["heartbeat"],
                },
            },
            timeout_ms=250,
            is_end=True,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response.debug_log",
        lambda *items: debug_messages.append(items),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response.capture_debug_sample",
        lambda label, payload: captured_samples.append((label, payload)),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        sleep_calls.append,
    )

    result = list(
        _get_chat_messages(
            downloader,
            {
                "continuation_info": {
                    "Top chat": "top-token",
                    "Live chat": "live-token",
                },
                "status": "live",
            },
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
                message_groups=["messages"],
            ),
        ),
    )

    assert result == []
    assert downloader.invalid_type_checks
    assert downloader.session.headers["authorization"] == "AUTH_TOKEN"
    assert downloader.session.headers["x-goog-visitor-id"] == "visitor-2"
    assert sleep_calls == [0.5]
    assert captured_samples == [
        (
            "youtube-unknown-continuation-heartbeat",
            {
                "continuation_key": "heartbeat",
                "continuation_entry": {"timeoutMs": 250},
                "payload_summary": {
                    "top_level_keys": ["continuationContents"],
                    "continuation_contents_keys": ["liveChatContinuation"],
                    "live_chat_keys": ["actions", "continuations"],
                    "actions_count": 0,
                    "continuation_keys": ["heartbeat"],
                },
            },
        ),
    ]
    assert debug_messages == [
        (
            "Unknown continuation: heartbeat",
            {"heartbeat": {"timeoutMs": 250}},
            {
                "payload_summary": {
                    "top_level_keys": ["continuationContents"],
                    "continuation_contents_keys": ["liveChatContinuation"],
                    "live_chat_keys": ["actions", "continuations"],
                    "actions_count": 0,
                    "continuation_keys": ["heartbeat"],
                },
            },
        ),
    ]


def test_chat_iteration_reraises_incomplete_continuation_when_fallback_unavailable(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    original_error = IncompleteContinuationError("original")

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            continuation_url="https://example.test/continuation",
            innertube_context={"client": {}},
            msg_filter=None,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(original_error),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._attempt_profile_fallback",
        lambda _downloader: False,
    )

    with pytest.raises(IncompleteContinuationError, match="original"):
        list(
            _get_chat_messages(
                downloader,
                {"continuation_info": {"Live chat": "token"}, "status": "live"},
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            )
        )


def test_chat_iteration_raises_when_live_chat_continuation_is_missing(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            continuation_url="https://example.test/continuation",
            innertube_context={"client": {}},
            msg_filter=None,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {"responseContext": {}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._handle_continuation_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.summarize_continuation_payload",
        lambda _yt_info: "summary",
    )

    with pytest.raises(IncompleteContinuationError, match="Summary: summary"):
        list(
            _get_chat_messages(
                downloader,
                {"continuation_info": {"Live chat": "token"}, "status": "live"},
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            )
        )


def test_chat_iteration_returns_immediately_when_action_processing_requests_stop(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            continuation_url="https://example.test/continuation",
            innertube_context={"client": {}},
            msg_filter=None,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {
            "continuationContents": {"liveChatContinuation": {"actions": [{"id": 1}]}}
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._handle_continuation_response",
        lambda *_args, **_kwargs: None,
    )

    def _stop_processing(*_args, **_kwargs):
        if False:
            yield {}
        return True

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._process_actions",
        _stop_processing,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._advance_continuation_loop",
        lambda *_args, **_kwargs: pytest.fail(
            "_advance_continuation_loop should not run after stop"
        ),
    )

    assert (
        list(
            _get_chat_messages(
                downloader,
                {"continuation_info": {"Live chat": "token"}, "status": "live"},
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            )
        )
        == []
    )


def test_chat_iteration_yields_chat_ended_when_clean_live_end(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    msg_filter = SimpleNamespace(should_add=lambda _message: True)

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            continuation_url="https://example.test/continuation",
            innertube_context={"client": {}},
            msg_filter=msg_filter,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_args, **_kwargs: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._handle_continuation_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._advance_continuation_loop",
        lambda *_args, **_kwargs: True,
    )

    assert list(
        _get_chat_messages(
            downloader,
            {"continuation_info": {"Live chat": "token"}, "status": "live"},
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
                message_groups=["messages"],
            ),
        )
    ) == [
        {
            "message_type": "chat_ended",
            "action_type": "chat_ended",
            "message": None,
        },
    ]


def test_chat_iteration_switches_profile_after_incomplete_continuation(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    call_state = {"count": 0}

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )

    def fake_continuation(_url, _session_post, _params, **_kwargs):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise IncompleteContinuationError("incomplete")
        return {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
            "responseContext": {},
        }

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        fake_continuation,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(timeout_ms=None, is_end=True),
    )

    list(
        _get_chat_messages(
            downloader,
            {
                "continuation_info": {"Top chat": "top", "Live chat": "live"},
                "status": "live",
            },
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
                message_groups=["messages"],
            ),
        ),
    )

    assert downloader.applied_profiles == ["youtube_android"]


def test_chat_iteration_live_updates_offset_from_message_timestamps(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    continuation_payloads = []
    responses = iter(
        [
            {
                "continuationContents": {
                    "liveChatContinuation": {"actions": [{"id": 1}]},
                },
                "responseContext": {},
            },
            {
                "continuationContents": {"liveChatContinuation": {"actions": []}},
                "responseContext": {},
            },
        ],
    )
    parse_results = iter(
        [
            _build_result(is_end=False, next_continuation="next-live"),
            _build_result(is_end=True, next_continuation=None),
        ],
    )

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context.get_live_start_time_ms",
        lambda: 1000,
    )

    def fake_get_continuation_info(_url, _session_post, _request, **kwargs):
        continuation_payloads.append(kwargs["json"])
        return next(responses)

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        fake_get_continuation_info,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.process_pipeline_action",
        lambda *_args, **_kwargs: SimpleNamespace(
            disposition="yield",
            message={"timestamp": 6_000_000},
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: next(parse_results),
    )

    messages = list(
        _get_chat_messages(
            downloader,
            {
                "continuation_info": {
                    "Top chat": "top-token",
                    "Live chat": "live-token",
                },
                "status": "live",
            },
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
                message_groups=["messages"],
            ),
        ),
    )

    assert messages == [
        {"timestamp": 6_000_000, "time_in_seconds": 5.0, "time_text": "0:05"},
    ]
    assert continuation_payloads[0] == {
        "context": {
            "client": {
                "visitorData": "visitor",
                "clientName": "WEB",
                "clientVersion": REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_web"][
                    "client"
                ]["clientVersion"],
                "hl": "en",
                "timeZone": "UTC",
                "utcOffsetMinutes": 0,
            },
        },
        "continuation": "live-token",
    }
    assert continuation_payloads[1] == {
        "context": {
            "client": {
                "visitorData": "visitor",
                "clientName": "WEB",
                "clientVersion": REQUEST_PROFILE_INNERTUBE_CONTEXTS["youtube_web"][
                    "client"
                ]["clientVersion"],
                "hl": "en",
                "timeZone": "UTC",
                "utcOffsetMinutes": 0,
            },
        },
        "continuation": "next-live",
        "currentPlayerState": {"playerOffsetMs": 0},
    }


def test_chat_iteration_replay_processes_actions_and_ends_page(
    monkeypatch,
) -> None:
    downloader = _DummyDownloader()
    process_calls = []
    end_page_calls = []
    continuation_requests = []
    state_updates = []

    class FakeTimeFilter:
        def end_page(self) -> None:
            end_page_calls.append("end")

    responses = iter(
        [
            {
                "continuationContents": {
                    "liveChatContinuation": {"actions": [{"id": 1}, {"id": 2}]},
                },
                "responseContext": {},
            },
            {
                "continuationContents": {"liveChatContinuation": {"actions": []}},
                "responseContext": {},
            },
        ],
    )

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_response._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_context.TimeRangeFilter",
        lambda *args, **kwargs: FakeTimeFilter(),
    )

    def fake_build_continuation_params(_context, state, *, is_replay):
        continuation_requests.append(state.continuation)
        return {"continuation": state.continuation}

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        fake_build_continuation_params,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_args, **_kwargs: next(responses),
    )

    pipeline_results = iter(
        [
            SimpleNamespace(disposition="skip", message=None),
            SimpleNamespace(disposition="yield", message={"message": "hi"}),
        ],
    )

    def fake_process_pipeline_action(action, offset, _msg_filter, _time_filter):
        process_calls.append((action, offset))
        return next(pipeline_results)

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.process_pipeline_action",
        fake_process_pipeline_action,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.parse_continuation_response",
        lambda _yt_info: _build_result(
            debug_info={"continuation_entry": {"timeoutMs": 0}},
            timeout_ms=None,
            is_end=False,
        ),
    )

    def fake_update_state_from_result(state, _result):
        state_updates.append(state.continuation)
        return state

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.update_state_from_result",
        fake_update_state_from_result,
    )

    messages = list(
        _get_chat_messages(
            downloader,
            {
                "continuation_info": {
                    "Top chat": "top-token",
                    "Live chat": "live-token",
                },
                "status": "past",
                "offset": 4.0,
            },
            {"INNERTUBE_API_KEY": "key"},
            ChatRequest(
                url="https://www.youtube.com/watch?v=abc",
                chat_type="live",
                message_groups=["messages"],
                start_time=2,
            ),
        ),
    )

    assert messages == [{"message": "hi"}]
    assert process_calls == [({"id": 1}, 4.0), ({"id": 2}, 4.0)]
    assert continuation_requests == ["live-token", "live-token"]
    assert state_updates == ["live-token"]
    assert end_page_calls == ["end"]


def test_user_chat_lookup_skips_ignored_and_non_live_videos_before_yield() -> None:
    class DummyChat:
        def __init__(self, video_id: str) -> None:
            self.chat = iter([{"message": "hello"}])
            self.title = "Target"
            self.id = video_id
            self.author = "Uploader"
            self._private = "ignored"

        def __iter__(self):
            return self.chat

    class DummyUsers(YouTubeChatUsersRetrievalMixin):
        def __init__(self) -> None:
            self._videos = iter(
                [
                    {"video_id": "old", "video_type": "VOD", "title": "Old"},
                    {
                        "video_id": "skip-me",
                        "video_type": "LIVE",
                        "title": "Ignored",
                    },
                    {
                        "video_id": "keep-me",
                        "video_type": "UPCOMING",
                        "title": "Target",
                    },
                ],
            )

        def get_user_videos(self, **_kwargs):
            return self._videos

        def get_chat_by_video_id(self, video_id, params):
            assert video_id == "keep-me"
            assert params.ignore == ["skip-me"]
            return DummyChat(video_id)

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
            return iter(
                [
                    {
                        "video_id": "keep-me",
                        "video_type": "LIVE",
                        "title": "Target",
                    },
                ],
            )

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

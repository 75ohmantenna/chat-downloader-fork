# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import RequestException

from chat_downloader import ChatDownloader
from chat_downloader.errors import (
    NoChatReplay,
    ParsingError,
    RetriesExceeded,
    UserNotFound,
    VideoUnavailable,
)
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.capture_manifest import replay_complete
from chat_downloader.runtime.runner import RunResult, execute_run
from chat_downloader.sites.models import Chat
from chat_downloader.sites.twitch import _replay_vod_loop, live_service, replay_service
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.twitch.graphql_client import _PersistedQueryUnavailable
from chat_downloader.sites.twitch.replay_transport import get_chat_messages_by_vod_id
from chat_downloader.sites.twitch.types import BadgeSet
from chat_downloader.utils.timed_generator import TimedGenerator, _PollingCancelled
from tests.twitch_third_helpers import chat_request


def test_clip_at_vod_offset_zero_retains_clip_duration() -> None:
    downloader = SimpleNamespace(
        _download_base_gql=Mock(
            return_value={
                "data": {
                    "clip": {
                        "video": {"id": "vod123"},
                        "videoOffsetSeconds": 0,
                        "durationSeconds": 45,
                        "title": "Opening clip",
                    }
                }
            }
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(return_value=iter(())),
    )
    replay_service.get_chat_by_clip_id(
        cast("Any", downloader), "clip123", ChatRequest(max_attempts=1)
    )
    assert downloader._get_chat_messages_by_vod_id.call_args.args[2:] == (45.0, 0.0)


@pytest.fixture
def downloader():
    return SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        _download_base_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(return_value=iter(())),
        retry=Mock(),
    )


def _request(**overrides):
    return chat_request(
        **{"url": "https://www.twitch.tv/videos/123", "max_attempts": 1, **overrides}
    )


def _edge(message_id, cursor="c1"):
    return {
        "__typename": "VideoCommentEdge",
        "cursor": cursor,
        "node": {
            "__typename": "Comment",
            "id": message_id,
            "message": {"fragments": [{"text": message_id}]},
        },
    }


def _response(edges, has_next=False):
    return (
        {"edges": edges, "pageInfo": {"hasNextPage": has_next}},
        {"creator": {"id": "creator-1", "channel": {"id": "1"}}},
    )


def _message(message_id, **extra):
    return {"message_type": "text_message", "message_id": message_id, **extra}


def _run(
    downloader,
    fetch,
    request=None,
    *,
    video_id="vod123",
    duration=120,
    diagnostics=None,
):
    return list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            video_id,
            request or _request(),
            max_duration=duration,
            fetch_messages=cast("replay_service._FetchMessages", fetch),
            diagnostics=diagnostics,
        )
    )


@pytest.mark.parametrize(
    ("kind", "metadata", "retry", "expected"),
    [
        ("vod", None, False, VideoUnavailable),
        ("clip", {"video": None, "title": "Expired Clip"}, False, NoChatReplay),
        (
            "vod",
            {"title": "Replay", "lengthSeconds": 12.5, "owner": {}},
            False,
            ("Replay", 12.5),
        ),
        (
            "vod",
            {
                "title": "Example VOD",
                "lengthSeconds": 123,
                "owner": {"id": "channel-123", "login": "streamer"},
            },
            True,
            ("Example VOD", 123),
        ),
        (
            "clip",
            {
                "video": {"id": "vod123"},
                "videoOffsetSeconds": 15,
                "durationSeconds": 45,
                "title": "Example Clip",
                "broadcaster": {"id": "channel-123", "login": "streamer"},
            },
            True,
            ("Example Clip (123)", 45),
        ),
    ],
    ids=["missing-vod", "expired-clip", "missing-owner", "vod-retry", "clip-retry"],
)
def test_replay_metadata(downloader, kind, metadata, retry, expected):
    response = {"data": {"video" if kind == "vod" else "clip": metadata}}
    download = (
        downloader._download_gql if kind == "vod" else downloader._download_base_gql
    )
    responses = [response] if kind == "clip" else [[response]]
    if retry:
        responses.insert(0, RequestException("temporary"))
    download.side_effect = responses
    get_chat = getattr(replay_service, f"get_chat_by_{kind}_id")
    request = _request(max_attempts=len(responses))
    if isinstance(expected, type):
        with pytest.raises(expected):
            get_chat(downloader, "123", request)
    else:
        chat = get_chat(downloader, "123", request)
        assert (chat.title, chat.duration) == expected
    assert downloader.retry.call_count == int(retry)
    if retry:
        downloader._update_badge_info.assert_called_once_with("streamer", "channel-123")
    else:
        downloader._update_badge_info.assert_not_called()


@pytest.mark.parametrize("kind", ["vod", "stream"])
@pytest.mark.parametrize(
    "malformed",
    [
        [],
        None,
        [None],
        [{}],
        [{"data": None}],
        [{"data": []}],
        [{"data": {}}],
        [{"data": {"video": "private-content", "user": "private-content"}}],
        [{"data": {"video": [], "user": []}}],
        [{"data": {}}, {"data": {}}],
    ],
)
def test_public_metadata_malformed_envelope_retries(kind, malformed):
    resource = "video" if kind == "vod" else "user"
    metadata = (
        {"title": "Recovered", "lengthSeconds": 12}
        if kind == "vod"
        else {
            "id": "channel-1",
            "stream": {"type": "live"},
            "lastBroadcast": {"title": "Recovered"},
        }
    )
    payloads = [malformed, [{"data": {resource: metadata}}]]
    with closing(TwitchChatDownloader()) as provider:
        provider._session_post = Mock(
            side_effect=[
                Mock(status_code=200, text="", json=Mock(return_value=payload))
                for payload in payloads
            ]
        )
        provider._update_badge_info = Mock()
        with patch.object(provider, "retry", wraps=provider.retry) as retry:
            chat = getattr(provider, f"get_chat_by_{kind}_id")(
                "123", _request(max_attempts=2, retry_timeout=0)
            )
        assert chat.title == "Recovered"
        assert provider._session_post.call_count == 2
        retry.assert_called_once()
        error = retry.call_args.kwargs["error"]
        assert isinstance(error, ParsingError)
        assert "private-content" not in str(error)
        chat.close()


@pytest.mark.parametrize("kind", ["vod", "stream"])
def test_public_metadata_envelope_exhaustion_is_bounded(kind):
    with closing(TwitchChatDownloader()) as provider:
        provider._session_post = Mock(
            return_value=Mock(status_code=200, text="", json=Mock(return_value=[]))
        )
        with pytest.raises(RetriesExceeded):
            getattr(provider, f"get_chat_by_{kind}_id")(
                "123", _request(max_attempts=2, retry_timeout=0)
            )
        assert provider._session_post.call_count == 2


@pytest.mark.parametrize("metadata", [None, {}])
@pytest.mark.parametrize(
    ("kind", "resource", "error"),
    [("vod", "video", VideoUnavailable), ("stream", "user", UserNotFound)],
)
def test_public_metadata_absent_resource_is_terminal(kind, resource, error, metadata):
    with closing(TwitchChatDownloader()) as provider:
        provider._session_post = Mock(
            return_value=Mock(
                status_code=200,
                text="",
                json=Mock(return_value=[{"data": {resource: metadata}}]),
            )
        )
        with patch.object(provider, "retry") as retry, pytest.raises(error):
            getattr(provider, f"get_chat_by_{kind}_id")(
                "123", _request(max_attempts=2, retry_timeout=0)
            )
        assert provider._session_post.call_count == 1
        retry.assert_not_called()


@pytest.mark.parametrize("kind", ["vod", "stream"])
@pytest.mark.parametrize(
    "error_type", [TypeError, IndexError, AttributeError, _PollingCancelled]
)
def test_metadata_collaborator_failures_propagate(downloader, kind, error_type):
    error = error_type()
    downloader._download_gql.side_effect = error
    service = replay_service if kind == "vod" else live_service
    with pytest.raises(error_type) as raised:
        getattr(service, f"get_chat_by_{kind}_id")(
            downloader, "123", _request(max_attempts=2)
        )
    assert raised.value is error
    downloader.retry.assert_not_called()
    assert downloader._download_gql.call_count == 1


def test_replay_service_iter_vod_chat_messages_retries_then_stops_on_empty_page(
    downloader,
):
    fetch = Mock(
        side_effect=[
            RequestException("temporary failure"),
            (None, {"creator": {"id": "creator-1"}}),
        ]
    )
    assert _run(downloader, fetch, _request(max_attempts=2)) == []
    downloader.retry.assert_called_once()


def test_mobile_replay_fallback_drives_full_multi_page_composition(downloader):
    def mobile_page(message_id, cursor, offset):
        node = {
            "__typename": "VideoComment",
            "id": message_id,
            "createdAt": "2026-08-31T00:00:00Z",
            "contentOffsetSeconds": offset,
            "commenter": {"id": "user-1", "login": "viewer", "displayName": "Viewer"},
            "message": {
                "fragments": [
                    {"text": "Kappa", "emote": {"from": 0, "emoteID": "25", "to": 4}}
                ],
                "userBadges": [{"setID": "subscriber", "version": "1"}],
            },
            "video": {"id": "vod-1", "owner": {"id": "owner-1"}},
        }
        return [
            {
                "data": {
                    "video": {"comments": {"edges": [{"cursor": cursor, "node": node}]}}
                }
            }
        ]

    download = Mock(
        side_effect=[
            _PersistedQueryUnavailable("rotated"),
            mobile_page("message-1", "cursor-1", 1),
            _PersistedQueryUnavailable("rotated"),
            mobile_page("message-2", "", 2),
        ]
    )

    badge_set = BadgeSet(
        global_badges={},
        channel_badges={
            "owner-1": {
                ("subscriber", "1"): {
                    "title": "Channel subscriber",
                    "image1x": "https://example.invalid/1.png",
                    "image2x": "https://example.invalid/2.png",
                    "image4x": "https://example.invalid/4.png",
                },
            }
        },
    )
    downloader._download_gql = download
    downloader.badge_cache.snapshot = lambda: badge_set
    result = _run(
        downloader, get_chat_messages_by_vod_id, video_id="vod-1", duration=30
    )
    assert [item["message_id"] for item in result] == ["message-1", "message-2"]
    assert result[0]["emotes"][0]["locations"] == "0-4"
    assert result[0]["author"]["badges"][0]["title"] == "Channel subscriber"
    calls = [call.args[0] for call in download.call_args_list]
    assert calls[2][0]["variables"] == {"videoID": "vod-1", "cursor": "cursor-1"}
    assert calls[3][0]["variables"] == {"vodId": "vod-1", "after": "cursor-1"}
    assert len(calls) == 4


def test_replay_service_iter_vod_chat_messages_handles_typenames_filters_and_stop(
    downloader,
):
    downloader.badge_cache.snapshot = lambda: {"badges": True}
    edges = [
        {"__typename": "UnexpectedEdge", "cursor": "c1", "node": {}},
        {"__typename": "VideoCommentEdge", "cursor": "c2", "node": None},
        {
            "__typename": "VideoCommentEdge",
            "cursor": "c3",
            "node": {"__typename": "UnexpectedNode"},
        },
        *[
            _edge(name, f"c{index}")
            for index, name in enumerate(["skip", "filtered", "kept", "stop"], start=4)
        ],
    ]
    fetch = Mock(return_value=_response(edges, True))
    time_filter, message_filter = Mock(), Mock()
    time_filter.check.side_effect = ["skip", None, None, "stop"]
    message_filter.should_add.side_effect = [False, True]
    parsed = [_message("skip", extra="x"), *map(_message, ["filtered", "kept", "stop"])]
    diagnostics: dict[str, object] = {}
    with (
        patch.object(_replay_vod_loop, "TimeRangeFilter", return_value=time_filter),
        patch.object(
            _replay_vod_loop.MessageFilter, "from_request", return_value=message_filter
        ),
        patch.object(replay_service, "_parse_item", side_effect=parsed),
        patch.object(
            replay_service,
            "build_known_comment_keys",
            return_value={"message_type", "message_id"},
        ),
        patch.object(replay_service, "debug_log") as debug_log,
        patch.object(replay_service.logger, "isEnabledFor", return_value=True),
        patch.object(replay_service, "capture_debug_sample") as capture,
    ):
        result = _run(downloader, fetch, _request(end_time=30), diagnostics=diagnostics)
    assert result == [_message("kept")]
    assert diagnostics["parse_error"] == 3
    assert (debug_log.call_count, capture.call_count) == (3, 3)
    capture.assert_any_call(
        "twitch-unknown-gql-shape",
        {"raw": edges[3], "unexpected_output_keys": ["extra"], "parsed": parsed[0]},
        sample_limit=10,
    )


@pytest.mark.parametrize("non_dict_edge", [False, True])
def test_replay_completed_page_logs_count_and_skips_non_dict_edges(
    downloader, non_dict_edge
):
    message_id = "msg1" if non_dict_edge else "kept"
    edges = ([None] if non_dict_edge else []) + [_edge(message_id)]
    response = _response(edges)
    if non_dict_edge:
        response = (response[0], {"creator": {"id": "c1"}})
    with (
        patch.object(replay_service, "_parse_item", return_value=_message(message_id)),
        patch.object(replay_service, "log") as log,
    ):
        result = _run(downloader, Mock(return_value=response))
    assert result == [_message(message_id)]
    log.assert_any_call("debug", "Total number of messages: 1")


@pytest.mark.parametrize(
    "url",
    ["https://www.twitch.tv/videos/123", "https://clips.twitch.tv/clip123"],
)
def test_replay_request_rejects_zero_attempts(url):
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(url=url, max_attempts=0)


@pytest.mark.parametrize(
    ("edges", "page_limit", "expected_calls"),
    [
        ([], 50, 3),
        (
            [
                {
                    "__typename": "VideoCommentEdge",
                    "node": {"__typename": "UnexpectedNode"},
                }
            ],
            10,
            1,
        ),
    ],
    ids=["empty-pages", "cursor-not-advancing"],
)
def test_iter_vod_stops_when_pagination_cannot_advance(
    downloader, edges, page_limit, expected_calls
):
    # Finite responses fail fast if empty-page or unchanged-cursor guards regress.
    fetch = Mock(side_effect=[_response(edges, True)] * page_limit)
    assert _run(downloader, fetch) == []
    assert fetch.call_count == expected_calls


@pytest.mark.parametrize(
    ("cursors", "terminal", "expected_ids", "complete"),
    [
        (["a", "a"], False, ["message-a"], False),
        (["a", "b", "a"], False, ["message-a", "message-b"], False),
        (["a", "a"], True, ["message-a", "terminal"], True),
    ],
    ids=["immediate-repeat", "historical-cycle", "terminal-repeated-cursor"],
)
def test_replay_cursor_progress_precedes_emission(
    downloader, cursors, terminal, expected_ids, complete
):
    pages = []
    for index, cursor in enumerate(cursors):
        is_terminal = terminal and index == len(cursors) - 1
        message_id = "terminal" if is_terminal else f"message-{cursor}"
        edge = _edge(message_id, cursor)
        edge["node"].update(
            {
                "createdAt": "2026-08-31T00:00:00Z",
                "contentOffsetSeconds": index + 1,
                "commenter": {
                    "id": "viewer-1",
                    "login": "viewer",
                    "displayName": "Viewer",
                },
                "message": {"fragments": [{"text": message_id}]},
            }
        )
        # Stalled pages must still account for malformed provider records.
        edges = [edge]
        if not terminal and index == len(cursors) - 1:
            edges = [None, {"node": {"__typename": "UnexpectedNode"}}, edge]
        comments, _ = _response(edges, has_next=not is_terminal)
        pages.append([{"data": {"video": {"comments": comments}}}])
    downloader._download_gql.side_effect = pages
    diagnostics: dict[str, object] = {}

    result = _run(downloader, get_chat_messages_by_vod_id, diagnostics=diagnostics)

    assert [item["message_id"] for item in result] == expected_ids
    assert [item["message"] for item in result] == expected_ids
    assert len({item["message_id"] for item in result}) == len(result)
    calls = downloader._download_gql.call_args_list
    assert len(calls) == len(cursors)
    assert [call.args[0][0]["variables"].get("cursor") for call in calls] == [
        None,
        *cursors[:-1],
    ]
    if complete:
        assert "termination_reason" not in diagnostics
    else:
        assert diagnostics["termination_reason"] == "pagination_stalled"
        assert diagnostics["parse_error"] == 2
    chat = Chat(status="past", diagnostics=diagnostics)
    chat.site = cast(
        "Any",
        SimpleNamespace(is_completed_replay_status=lambda status: status == "past"),
    )
    run_result = RunResult(
        success=True,
        termination_reason=str(diagnostics.get("termination_reason", "completed")),
    )
    run_result.observe_provider(chat, None)
    assert replay_complete(chat, run_result) is complete


def test_replay_retries_empty_page_without_losing_cursor_progress(downloader):
    fetch = Mock(
        side_effect=[
            _response([_edge("first", "a")], True),
            _response([], True),
            _response([_edge("second", "b")], True),
            _response([], False),
        ]
    )
    diagnostics: dict[str, object] = {}
    result = _run(downloader, fetch, diagnostics=diagnostics)
    assert [item["message_id"] for item in result] == ["first", "second"]
    assert [call.args[3] for call in fetch.call_args_list] == [None, "a", "a", "b"]
    assert "termination_reason" not in diagnostics


def test_replay_detects_old_cycle_after_large_cursor_history(downloader):
    page_count = 2048

    def pages():
        for index in [*range(page_count), 0]:
            cursor = f"{index}:" + "x" * 2048
            edge = _edge(f"message-{index}", cursor)
            edge["node"].update(
                {
                    "contentOffsetSeconds": 1,
                    "message": {"fragments": [{"text": f"record {index}"}]},
                }
            )
            comments, _ = _response([edge], has_next=True)
            yield [{"data": {"video": {"comments": comments}}}]

    downloader._download_gql.side_effect = pages()
    diagnostics: dict[str, object] = {}
    result = _run(downloader, get_chat_messages_by_vod_id, diagnostics=diagnostics)

    assert [item["message_id"] for item in result] == [
        f"message-{index}" for index in range(page_count)
    ]
    assert result[-1]["message"] == f"record {page_count - 1}"
    assert downloader._download_gql.call_count == page_count + 1
    assert diagnostics["termination_reason"] == "pagination_stalled"


def test_timed_replay_closes_suspended_source_from_consumer(downloader, monkeypatch):
    suspended = Event()
    release = Event()
    publish = TimedGenerator._publish_result
    published = 0

    def pause_second_publish(timed, result):
        nonlocal published
        published += 1
        if published == 2:
            suspended.set()
            assert release.wait(timeout=2)
        return publish(timed, result)

    monkeypatch.setattr(TimedGenerator, "_publish_result", pause_second_publish)
    fetch = Mock(
        side_effect=[
            _response([_edge("first", "a")], True),
            _response([_edge("second", "b")], True),
        ]
    )
    source = replay_service.iter_vod_chat_messages(
        cast("Any", downloader),
        "vod123",
        _request(),
        max_duration=120,
        fetch_messages=cast("replay_service._FetchMessages", fetch),
    )
    timed = TimedGenerator(source)
    try:
        assert suspended.wait(timeout=2)
        assert next(timed)["message_id"] == "first"
        # The replay DB was opened on the worker; this closes it on the caller.
        timed.close()
        assert source.gi_frame is None
    finally:
        release.set()
        timed.close()
        timed._worker.join(timeout=2)
    assert not timed._worker.is_alive()
    assert fetch.call_count == 2


@pytest.mark.parametrize("stop", ["timeout", "close"])
@pytest.mark.parametrize("offset", [None, 15.0], ids=["vod", "clip"])
def test_timed_replay_stops_before_filtered_page_advances(
    downloader, monkeypatch, stop, offset
):
    entered = Event()
    release = Event()
    connections = []
    connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(replay_service.sqlite3, "connect", track_connection)
    edge = _edge("filtered", "fresh-cursor")
    edge["node"].update(
        {
            "createdAt": "2026-08-31T00:00:00Z",
            "contentOffsetSeconds": (offset or 0) + 1,
            "commenter": {"id": "viewer-1", "login": "viewer"},
            "message": {"fragments": [{"text": "excluded text"}]},
        }
    )

    def fetch_page(_post, _download, _vod_id, cursor, _content_offset):
        if cursor is None:
            entered.set()
            assert release.wait(timeout=2)
            return _response([edge], True)
        return _response([], False)

    fetch = Mock(side_effect=fetch_page)
    diagnostics: dict[str, object] = {}
    source = replay_service.iter_vod_chat_messages(
        cast("Any", downloader),
        "vod123",
        _request(message_groups=["subscriptions"]),
        max_duration=120,
        offset=offset,
        fetch_messages=cast("replay_service._FetchMessages", fetch),
        diagnostics=diagnostics,
    )
    on_timeout = Mock()
    timed = TimedGenerator(source, timeout=60, on_timeout=on_timeout)
    try:
        assert entered.wait(timeout=2)
        if stop == "timeout":
            timed._timeout_deadline = 0
            timed._timeout_expired.set()
            with pytest.raises(StopIteration):
                next(timed)
        else:
            timed.close()
        assert timed._stop_requested.is_set()
        assert source.gi_frame is not None
    finally:
        release.set()
        timed.close()
        timed._worker.join(timeout=2)

    assert not timed._worker.is_alive()
    assert source.gi_frame is None
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert fetch.call_count == 1
    assert diagnostics == {}
    assert timed._result_queue.empty()
    if stop == "timeout":
        assert timed._finish_reason == "timeout"
        assert timed.deadline_prefetch_summary() == (0, True)
        on_timeout.assert_called_once_with()
    else:
        on_timeout.assert_not_called()


def test_timed_replay_stops_between_filtered_edges(downloader, monkeypatch):
    entered = Event()
    release = Event()
    process_edge = replay_service._process_vod_edge
    processed = []

    def process_then_pause(*args, **kwargs):
        result = process_edge(*args, **kwargs)
        processed.append(args[0]["node"]["id"])
        if len(processed) == 1:
            assert result == (None, "skip")
            entered.set()
            assert release.wait(timeout=2)
        return result

    monkeypatch.setattr(replay_service, "_process_vod_edge", process_then_pause)
    fetch = Mock(
        side_effect=[
            _response([_edge("first", "a"), _edge("second", "b")], True),
            _response([], False),
        ]
    )
    source = replay_service.iter_vod_chat_messages(
        cast("Any", downloader),
        "vod123",
        _request(message_groups=["subscriptions"]),
        max_duration=120,
        fetch_messages=cast("replay_service._FetchMessages", fetch),
    )
    timed = TimedGenerator(source)
    try:
        assert entered.wait(timeout=2)
        timed.close()
    finally:
        release.set()
        timed.close()
        timed._worker.join(timeout=2)

    assert not timed._worker.is_alive()
    assert source.gi_frame is None
    assert processed == ["first"]
    assert fetch.call_count == 1
    assert timed._result_queue.empty()


@pytest.mark.parametrize("metadata", [False, True], ids=["page", "metadata"])
def test_timed_replay_does_not_retry_failed_fetch_after_close(downloader, metadata):
    entered = Event()
    release = Event()

    def fail_after_close(*_args):
        entered.set()
        assert release.wait(timeout=2)
        raise RequestException("request failed after cancellation")

    fetch = Mock(side_effect=fail_after_close)
    request = _request(max_attempts=2)
    if metadata:
        downloader._download_gql.side_effect = fail_after_close

        def source_items():
            yield replay_service.get_chat_by_vod_id(downloader, "vod123", request)

        source = source_items()
    else:
        source = replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            "vod123",
            request,
            max_duration=120,
            fetch_messages=cast("replay_service._FetchMessages", fetch),
        )
    timed = TimedGenerator(source)
    try:
        assert entered.wait(timeout=2)
        timed.close()
    finally:
        release.set()
        timed.close()
        timed._worker.join(timeout=2)

    assert not timed._worker.is_alive()
    assert source.gi_frame is None
    downloader.retry.assert_not_called()
    assert (downloader._download_gql if metadata else fetch).call_count == 1


@pytest.mark.parametrize("edges", [[], [_edge("m1", "")]])
def test_stalled_twitch_replay_is_not_certified_complete(downloader, edges):
    diagnostics: dict[str, object] = {}
    with patch.object(replay_service, "_parse_item", return_value=_message("m1")):
        _run(
            downloader,
            Mock(return_value=_response(edges, True)),
            diagnostics=diagnostics,
        )
    chat = Chat(status="past", diagnostics=diagnostics)
    classifier = SimpleNamespace(
        is_completed_replay_status=lambda status: status == "past"
    )
    chat.site = cast("Any", classifier)
    assert diagnostics["termination_reason"] == "pagination_stalled"
    result = RunResult(
        success=True, termination_reason=str(diagnostics["termination_reason"])
    )
    result.observe_provider(chat, None)
    assert not replay_complete(chat, result)


def test_missing_comments_mid_pagination_is_retryable(downloader):
    downloader._download_gql.side_effect = [
        [{"data": {"video": {"comments": {"edges": [_edge("m1")]}}}}],
        [{"data": {"video": {"comments": None}}}],
    ]
    get_chat_messages_by_vod_id(None, downloader._download_gql, "123", None, 0)
    with pytest.raises(ParsingError, match="comments disappeared"):
        get_chat_messages_by_vod_id(
            None, downloader._download_gql, "123", "cursor-1", 0
        )


def test_missing_comments_after_first_page_marks_replay_incomplete(downloader):
    diagnostics: dict[str, object] = {}
    fetch = Mock(side_effect=[_response([_edge("m1")], True), (None, None)])
    with patch.object(replay_service, "_parse_item", return_value=_message("m1")):
        assert _run(downloader, fetch, diagnostics=diagnostics) == [_message("m1")]
    assert diagnostics["termination_reason"] == "pagination_stalled"


@pytest.mark.parametrize("video", [None, {"comments": {}}, {"comments": {"edges": []}}])
def test_initial_replay_response_must_establish_completeness(
    monkeypatch, tmp_path, video
):
    monkeypatch.setattr(
        TwitchChatDownloader,
        "_download_gql",
        Mock(
            side_effect=[
                [
                    {
                        "data": {
                            "video": {
                                "id": "123",
                                "title": "Video",
                                "lengthSeconds": 60,
                            }
                        }
                    }
                ],
                [{"data": {"video": video}}],
            ]
        ),
    )
    manifest = tmp_path / "manifest.json"
    result = execute_run(
        ChatDownloader,
        url="https://www.twitch.tv/videos/123",
        proxy="",
        quiet=True,
        max_attempts=1,
        require_complete=True,
        run_manifest=str(manifest),
    )
    expected = video == {"comments": {"edges": []}}
    assert result.success is expected
    assert json.loads(manifest.read_text())["replay_complete"] is expected
    assert result.message_count == 0


@pytest.mark.parametrize("fallback", [False, True])
def test_missing_video_mid_pagination_raises(downloader, fallback):
    if fallback:
        downloader._download_gql.side_effect = [
            _PersistedQueryUnavailable("rotated"),
            [{"data": {"video": {"comments": None}}}],
        ]
    else:
        downloader._download_gql.return_value = [{"data": {"video": None}}]
    with pytest.raises(ParsingError, match="disappeared"):
        get_chat_messages_by_vod_id(
            None, downloader._download_gql, "123", "cursor-1", 0
        )


@pytest.mark.parametrize("require_complete", [False, True])
def test_malformed_fragment_preserves_replay_output_and_reports_loss(
    monkeypatch, tmp_path, require_complete
):
    fixture = Path(__file__).parent / "fixtures/twitch/replay_malformed_fragments.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    download = Mock(
        side_effect=[
            [{"data": {"video": {"title": "Replay", "lengthSeconds": 60}}}],
            payload["raw"],
        ]
    )
    monkeypatch.setattr(TwitchChatDownloader, "_download_gql", download)
    output = tmp_path / "chat.jsonl"
    manifest = tmp_path / "manifest.json"
    result = execute_run(
        ChatDownloader,
        url="https://www.twitch.tv/videos/123",
        output=str(output),
        run_manifest=str(manifest),
        proxy="",
        quiet=True,
        max_attempts=3,
        require_complete=require_complete,
    )
    assert result.success is not require_complete
    assert result.message_count == 2
    assert [
        json.loads(line)["message_id"] for line in output.read_text().splitlines()
    ] == ["before", "after"]
    report = json.loads(manifest.read_text())
    assert report["provider_diagnostics"]["parse_error"] == 1
    assert not report["replay_complete"]
    assert download.call_count == 2


@pytest.mark.parametrize(
    "message",
    [
        None,
        [],
        "not-an-object",
        {},
        {"fragments": None},
        {"fragments": {}},
        {"fragments": "not-a-list"},
        {"fragments": [None]},
        {"fragments": [[]]},
        {"fragments": [{}]},
        {"fragments": [{"text": None}]},
        {"fragments": [{"text": 1}]},
        {"fragments": [{"text": []}]},
    ],
)
def test_malformed_nested_message_skips_only_its_record(downloader, message):
    edges = [_edge("before"), _edge("malformed"), _edge("after")]
    edges[1]["node"]["message"] = message
    diagnostics: dict[str, object] = {}
    fetch = Mock(side_effect=[_response(edges)])
    result = _run(downloader, fetch, diagnostics=diagnostics)
    assert [item["message_id"] for item in result] == ["before", "after"]
    assert diagnostics == {"parse_error": 1}
    fetch.assert_called_once()
    downloader.retry.assert_not_called()


@pytest.mark.parametrize("diagnostics", [None, {}])
def test_missing_message_is_a_record_parse_error(downloader, diagnostics):
    edge = _edge("malformed")
    del edge["node"]["message"]
    assert (
        _run(downloader, Mock(return_value=_response([edge])), diagnostics=diagnostics)
        == []
    )
    if diagnostics is not None:
        assert diagnostics == {"parse_error": 1}


@pytest.mark.parametrize(
    "error_type", [TypeError, ValueError, RuntimeError, ParsingError]
)
def test_message_enrichment_collaborator_errors_are_not_swallowed_or_retried(
    downloader, monkeypatch, error_type
):
    from chat_downloader.sites.twitch.parsing import messages

    edges = [_edge("before"), _edge("broken"), _edge("after")]
    edges[1]["node"]["message"]["fragments"] = [
        {"text": "Kappa", "emote": {"emoteID": "25", "id": "emote;0;4"}}
    ]
    error = error_type("collaborator failure")
    monkeypatch.setattr(messages, "_generate_emote_image_list", Mock(side_effect=error))
    fetch = Mock(side_effect=[_response(edges)])
    diagnostics: dict[str, object] = {}
    source = replay_service.iter_vod_chat_messages(
        cast("Any", downloader),
        "123",
        _request(max_attempts=3),
        max_duration=60,
        fetch_messages=cast("replay_service._FetchMessages", fetch),
        diagnostics=diagnostics,
    )
    assert next(source)["message_id"] == "before"
    with pytest.raises(error_type, match="collaborator failure") as caught:
        next(source)
    assert caught.value is error
    assert diagnostics == {}
    fetch.assert_called_once()
    downloader.retry.assert_not_called()

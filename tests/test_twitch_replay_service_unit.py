# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import NoChatReplay, VideoUnavailable
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import _replay_vod_loop, replay_service
from chat_downloader.sites.twitch.replay_service import _process_vod_edge


def test_replay_service_get_chat_by_vod_id_raises_when_video_missing() -> None:
    downloader = SimpleNamespace(
        _download_gql=Mock(return_value=[{"data": {"video": None}}]),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(),
        retry=Mock(),
    )

    with pytest.raises(VideoUnavailable):
        replay_service.get_chat_by_vod_id(
            cast("Any", downloader),
            "vod123",
            ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=1),
        )

    downloader._update_badge_info.assert_not_called()


def test_replay_service_get_chat_by_vod_id_allows_missing_owner_login() -> None:
    downloader = SimpleNamespace(
        _download_gql=Mock(
            return_value=[
                {
                    "data": {
                        "video": {
                            "title": "Replay",
                            "lengthSeconds": 12.5,
                            "owner": {},
                        },
                    },
                },
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(return_value=iter(())),
        retry=Mock(),
    )
    request = ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=1)

    chat = replay_service.get_chat_by_vod_id(cast("Any", downloader), "123", request)

    assert chat.title == "Replay"
    assert chat.duration == 12.5
    downloader._update_badge_info.assert_not_called()
    downloader._get_chat_messages_by_vod_id.assert_called_once_with(
        "123",
        request,
        12.5,
    )


def test_replay_service_iter_vod_chat_messages_retries_then_stops_on_empty_page() -> (
    None
):
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=2,
        message_groups=["messages"],
    )
    fetch_messages = Mock(
        side_effect=[
            RequestException("temporary failure"),
            (None, {"creator": {"id": "creator-1"}}),
        ],
    )

    result = list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            "vod123",
            request,
            max_duration=120,
            fetch_messages=cast("replay_service._FetchMessages", fetch_messages),
        ),
    )

    assert result == []
    downloader.retry.assert_called_once()


def test_replay_service_iter_vod_chat_messages_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(
            url="https://www.twitch.tv/videos/123",
            max_attempts=0,
            message_groups=["messages"],
        )


def test_replay_service_iter_vod_chat_messages_handles_typenames_filters_and_stop() -> (
    None
):
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=lambda: {"badges": True}),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
        end_time=30,
    )
    comments = {
        "edges": [
            {"__typename": "UnexpectedEdge", "cursor": "c1", "node": {}},
            {"__typename": "VideoCommentEdge", "cursor": "c2", "node": None},
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c3",
                "node": {"__typename": "UnexpectedNode"},
            },
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c4",
                "node": {"__typename": "Comment", "id": "skip"},
            },
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c5",
                "node": {"__typename": "Comment", "id": "filtered"},
            },
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c6",
                "node": {"__typename": "Comment", "id": "kept"},
            },
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c7",
                "node": {"__typename": "Comment", "id": "stop"},
            },
        ],
        "pageInfo": {"hasNextPage": True},
    }
    fetch_messages = cast(
        "replay_service._FetchMessages",
        Mock(
            return_value=(
                comments,
                {"creator": {"id": "creator-1", "channel": {"id": "1"}}},
            ),
        ),
    )
    fake_time_filter = Mock()
    fake_time_filter.check.side_effect = ["skip", None, None, "stop"]
    fake_msg_filter = Mock()
    fake_msg_filter.should_add.side_effect = [False, True]
    parsed_messages = [
        {"message_type": "text_message", "message_id": "skip", "extra": "x"},
        {"message_type": "text_message", "message_id": "filtered"},
        {"message_type": "text_message", "message_id": "kept"},
        {"message_type": "text_message", "message_id": "stop"},
    ]

    with (
        patch.object(
            _replay_vod_loop, "TimeRangeFilter", return_value=fake_time_filter
        ),
        patch.object(_replay_vod_loop, "MessageFilter", return_value=fake_msg_filter),
        patch.object(replay_service, "_parse_item", side_effect=parsed_messages),
        patch.object(
            replay_service,
            "build_known_comment_keys",
            return_value={"message_type", "message_id"},
        ),
        patch.object(replay_service, "debug_log") as mock_debug_log,
    ):
        result = list(
            replay_service.iter_vod_chat_messages(
                cast("Any", downloader),
                "vod123",
                request,
                max_duration=120,
                fetch_messages=fetch_messages,
            ),
        )

    assert result == [{"message_type": "text_message", "message_id": "kept"}]
    mock_debug_log.assert_called_once()


def test_replay_service_iter_vod_chat_messages_logs_count_on_completed_page() -> None:
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )
    comments = {
        "edges": [
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c1",
                "node": {"__typename": "Comment", "id": "kept"},
            },
        ],
        "pageInfo": {"hasNextPage": False},
    }

    with (
        patch.object(
            replay_service,
            "_parse_item",
            return_value={"message_type": "text_message", "message_id": "kept"},
        ),
        patch.object(replay_service, "log") as mock_log,
    ):
        result = list(
            replay_service.iter_vod_chat_messages(
                cast("Any", downloader),
                "vod123",
                request,
                max_duration=120,
                fetch_messages=cast(
                    "replay_service._FetchMessages",
                    Mock(
                        return_value=(
                            comments,
                            {
                                "creator": {
                                    "id": "creator-1",
                                    "channel": {"id": "1"},
                                }
                            },
                        ),
                    ),
                ),
            ),
        )

    assert result == [{"message_type": "text_message", "message_id": "kept"}]
    mock_log.assert_any_call("debug", "Total number of messages: 1")


def test_replay_service_get_chat_by_vod_id_retries_before_success() -> None:
    downloader = SimpleNamespace(
        _download_gql=Mock(
            side_effect=[
                RequestException("temporary"),
                [
                    {
                        "data": {
                            "video": {
                                "title": "Example VOD",
                                "lengthSeconds": 123,
                                "owner": {"login": "streamer"},
                            },
                        },
                    },
                ],
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    chat = replay_service.get_chat_by_vod_id(
        cast("Any", downloader),
        "vod123",
        ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=2),
    )

    assert chat.title == "Example VOD"
    downloader.retry.assert_called_once()
    downloader._update_badge_info.assert_called_once_with("streamer")


def test_replay_service_get_chat_by_vod_id_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=0)


def test_replay_service_get_chat_by_clip_id_raises_when_replay_missing() -> None:
    downloader = SimpleNamespace(
        _download_base_gql=Mock(
            return_value={
                "data": {
                    "clip": {
                        "video": None,
                        "title": "Expired Clip",
                    },
                },
            },
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(),
        retry=Mock(),
    )

    with pytest.raises(NoChatReplay):
        replay_service.get_chat_by_clip_id(
            cast("Any", downloader),
            "expired-clip",
            ChatRequest(url="https://clips.twitch.tv/expired-clip", max_attempts=1),
        )

    downloader._update_badge_info.assert_not_called()


def test_replay_service_get_chat_by_clip_id_retries_before_success() -> None:
    downloader = SimpleNamespace(
        _download_base_gql=Mock(
            side_effect=[
                RequestException("temporary"),
                {
                    "data": {
                        "clip": {
                            "video": {"id": "vod123"},
                            "videoOffsetSeconds": 15,
                            "durationSeconds": 45,
                            "title": "Example Clip",
                            "broadcaster": {"login": "streamer"},
                        },
                    },
                },
            ],
        ),
        _update_badge_info=Mock(),
        _get_chat_messages_by_vod_id=Mock(return_value=iter(())),
        retry=Mock(),
    )

    chat = replay_service.get_chat_by_clip_id(
        cast("Any", downloader),
        "clip123",
        ChatRequest(url="https://clips.twitch.tv/clip123", max_attempts=2),
    )

    assert chat.title == "Example Clip (clip123)"
    downloader.retry.assert_called_once()
    downloader._update_badge_info.assert_called_once_with("streamer")


def test_replay_service_get_chat_by_clip_id_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(url="https://clips.twitch.tv/clip123", max_attempts=0)


def test_iter_vod_stops_on_repeated_empty_pages_with_has_next_page() -> None:
    """Pagination stops on empty edges even when hasNextPage=true."""
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )
    empty_response = (
        {"edges": [], "pageInfo": {"hasNextPage": True}},
        {"creator": {"id": "creator-1", "channel": {"id": "1"}}},
    )
    # If the guard didn't fire, this generator would loop forever; cap to a
    # finite list so the test fails fast on regression.
    fetch_messages = Mock(side_effect=[empty_response] * 50)

    list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            "vod123",
            request,
            max_duration=120,
            fetch_messages=cast("replay_service._FetchMessages", fetch_messages),
        ),
    )

    assert fetch_messages.call_count == 3


def test_iter_vod_stops_when_cursor_does_not_advance() -> None:
    """If a non-empty page returns the same cursor as before, stop."""
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )
    stuck_response = (
        {
            "edges": [
                {
                    "__typename": "VideoCommentEdge",
                    # No "cursor" key on edges → outer `cursor` never advances.
                    "node": {"__typename": "UnexpectedNode"},
                },
            ],
            "pageInfo": {"hasNextPage": True},
        },
        {"creator": {"id": "creator-1", "channel": {"id": "1"}}},
    )
    fetch_messages = Mock(side_effect=[stuck_response] * 10)

    list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            "vod123",
            request,
            max_duration=120,
            fetch_messages=cast("replay_service._FetchMessages", fetch_messages),
        ),
    )

    # Loop runs once, then stops because cursor didn't advance.
    assert fetch_messages.call_count == 1


# ── _process_vod_edge direct unit tests ──────────────────────────────────────


def _tf(result: str) -> Any:
    """Return a minimal time-filter mock that always returns *result*."""
    return type("_TF", (), {"check": lambda self, d: result})()


def _mf(result: bool) -> Any:
    """Return a minimal message-filter mock that always returns *result*."""
    return type("_MF", (), {"should_add": lambda self, d: result})()


_LOG = logging.getLogger(__name__)


@pytest.mark.parametrize("edge_typename", ["Unexpected", "SomeEdge"])
def test_process_vod_edge_skips_unrecognised_edge_typename(
    edge_typename: str,
) -> None:
    edge = {"__typename": edge_typename, "node": {"__typename": "Comment"}}
    data, disposition = _process_vod_edge(
        edge, 0.0, None, None, _tf("yield"), _mf(True), _LOG
    )
    assert data is None
    assert disposition == "skip"


def test_process_vod_edge_skips_when_node_absent() -> None:
    edge: dict[str, Any] = {"__typename": "VideoCommentEdge"}
    data, disposition = _process_vod_edge(
        edge, 0.0, None, None, _tf("yield"), _mf(True), _LOG
    )
    assert data is None
    assert disposition == "skip"


@pytest.mark.parametrize("node_typename", ["SomeNode", "OtherNode"])
def test_process_vod_edge_skips_unrecognised_node_typename(
    node_typename: str,
) -> None:
    edge = {
        "__typename": "VideoCommentEdge",
        "node": {"__typename": node_typename},
    }
    data, disposition = _process_vod_edge(
        edge, 0.0, None, None, _tf("yield"), _mf(True), _LOG
    )
    assert data is None
    assert disposition == "skip"


@pytest.mark.parametrize("filter_result", ["skip", "stop"])
def test_process_vod_edge_honours_time_filter_disposition(
    filter_result: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.replay_service._parse_item",
        lambda node, offset, creator_id, badge_set: {},
    )
    edge = {"__typename": "VideoCommentEdge", "node": {"__typename": "Comment"}}
    data, disposition = _process_vod_edge(
        edge, 0.0, None, None, _tf(filter_result), _mf(True), _LOG
    )
    assert data is None
    assert disposition == filter_result


def test_process_vod_edge_skips_when_msg_filter_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.replay_service._parse_item",
        lambda node, offset, creator_id, badge_set: {},
    )
    edge = {"__typename": "VideoCommentEdge", "node": {"__typename": "Comment"}}
    data, disposition = _process_vod_edge(
        edge, 0.0, None, None, _tf("yield"), _mf(False), _LOG
    )
    assert data is None
    assert disposition == "skip"


def test_process_vod_edge_yields_data_when_all_filters_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed: dict[str, Any] = {"id": "abc", "author": "user1"}
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.replay_service._parse_item",
        lambda node, offset, creator_id, badge_set: dict(parsed),
    )
    edge = {
        "__typename": "VideoCommentEdge",
        "node": {"__typename": "VideoComment"},
    }
    data, disposition = _process_vod_edge(
        edge, 5.0, "ch123", None, _tf("yield"), _mf(True), _LOG
    )
    assert data == parsed
    assert disposition == "yield"


def test_iter_vod_chat_messages_skips_non_dict_edge_items() -> None:
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )
    comments = {
        "edges": [
            None,
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c1",
                "node": {"__typename": "Comment", "id": "msg1"},
            },
        ],
        "pageInfo": {"hasNextPage": False},
    }
    fetch_messages = Mock(return_value=(comments, {"creator": {"id": "c1"}}))

    with patch.object(
        replay_service,
        "_parse_item",
        return_value={"message_type": "text_message", "message_id": "msg1"},
    ):
        result = list(
            replay_service.iter_vod_chat_messages(
                cast("Any", downloader),
                "vod123",
                request,
                max_duration=120,
                fetch_messages=cast("replay_service._FetchMessages", fetch_messages),
            ),
        )

    assert len(result) == 1
    assert result[0]["message_id"] == "msg1"


def test_iter_vod_chat_messages_raises_when_yield_edge_has_none_data() -> None:
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=Mock(),
        badge_cache=SimpleNamespace(snapshot=dict),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )
    comments = {
        "edges": [
            {
                "__typename": "VideoCommentEdge",
                "cursor": "c1",
                "node": {"__typename": "Comment", "id": "msg1"},
            },
        ],
        "pageInfo": {"hasNextPage": False},
    }
    fetch_messages = Mock(return_value=(comments, {"creator": {"id": "c1"}}))

    with (
        patch.object(
            replay_service,
            "_process_vod_edge",
            return_value=(None, "yield"),
        ),
        pytest.raises(ValueError, match="Unexpected None data"),
    ):
        list(
            replay_service.iter_vod_chat_messages(
                cast("Any", downloader),
                "vod123",
                request,
                max_duration=120,
                fetch_messages=cast("replay_service._FetchMessages", fetch_messages),
            ),
        )

# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import NoChatReplay, VideoUnavailable
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import _replay_vod_loop, replay_service
from chat_downloader.sites.twitch.graphql_client import _PersistedQueryUnavailable
from chat_downloader.sites.twitch.replay_transport import get_chat_messages_by_vod_id
from chat_downloader.sites.twitch.types import BadgeSet


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
    return ChatRequest(
        **{
            "url": "https://www.twitch.tv/videos/123",
            "max_attempts": 1,
            "message_groups": ["messages"],
            **overrides,
        }
    )


def _edge(message_id, cursor="c1"):
    return {
        "__typename": "VideoCommentEdge",
        "cursor": cursor,
        "node": {"__typename": "Comment", "id": message_id},
    }


def _response(edges, has_next=False):
    return (
        {"edges": edges, "pageInfo": {"hasNextPage": has_next}},
        {"creator": {"id": "creator-1", "channel": {"id": "1"}}},
    )


def _message(message_id, **extra):
    return {"message_type": "text_message", "message_id": message_id, **extra}


def _run(downloader, fetch, request=None, *, video_id="vod123", duration=120):
    return list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            video_id,
            request or _request(),
            max_duration=duration,
            fetch_messages=cast("replay_service._FetchMessages", fetch),
        )
    )


@pytest.mark.parametrize("kind", ["vod", "clip"])
def test_replay_metadata_missing(downloader, kind):
    if kind == "vod":
        downloader._download_gql.return_value = [{"data": {"video": None}}]
        get_chat, error = replay_service.get_chat_by_vod_id, VideoUnavailable
        request = _request()
        identifier = "vod123"
    else:
        downloader._download_base_gql.return_value = {
            "data": {"clip": {"video": None, "title": "Expired Clip"}},
        }
        get_chat, error = replay_service.get_chat_by_clip_id, NoChatReplay
        request = _request(url="https://clips.twitch.tv/expired-clip")
        identifier = "expired-clip"
    with pytest.raises(error):
        get_chat(downloader, identifier, request)
    downloader._update_badge_info.assert_not_called()


def test_replay_service_get_chat_by_vod_id_allows_missing_owner_login(downloader):
    downloader._download_gql.return_value = [
        {
            "data": {
                "video": {
                    "title": "Replay",
                    "lengthSeconds": 12.5,
                    "owner": {},
                }
            }
        }
    ]
    chat = replay_service.get_chat_by_vod_id(downloader, "123", _request())
    assert (chat.title, chat.duration) == ("Replay", 12.5)
    downloader._update_badge_info.assert_not_called()


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

    responses = [
        _PersistedQueryUnavailable("rotated"),
        mobile_page("message-1", "cursor-1", 1),
        _PersistedQueryUnavailable("rotated"),
        mobile_page("message-2", "", 2),
    ]
    calls = []

    def download(query):
        calls.append(query)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

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
    assert calls[2][0]["variables"] == {"videoID": "vod-1", "cursor": "cursor-1"}
    assert calls[3][0]["variables"] == {"vodId": "vod-1", "after": "cursor-1"}
    assert responses == []


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
        result = _run(downloader, fetch, _request(end_time=30))
    assert result == [_message("kept")]
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


@pytest.mark.parametrize("kind", ["vod", "clip"])
def test_replay_service_retries_metadata_before_success(downloader, kind):
    owner = {"id": "channel-123", "login": "streamer"}
    if kind == "vod":
        metadata = {"title": "Example VOD", "lengthSeconds": 123, "owner": owner}
        response = [{"data": {"video": metadata}}]
        download, get_chat = downloader._download_gql, replay_service.get_chat_by_vod_id
        title = "Example VOD"
    else:
        metadata = {
            "video": {"id": "vod123"},
            "videoOffsetSeconds": 15,
            "durationSeconds": 45,
            "title": "Example Clip",
            "broadcaster": owner,
        }
        response = {"data": {"clip": metadata}}
        download, get_chat = (
            downloader._download_base_gql,
            replay_service.get_chat_by_clip_id,
        )
        title = "Example Clip (123)"
    download.side_effect = [RequestException("temporary"), response]
    chat = get_chat(
        downloader, "123", ChatRequest(url="https://twitch.tv", max_attempts=2)
    )
    assert chat.title == title
    downloader.retry.assert_called_once()
    downloader._update_badge_info.assert_called_once_with("streamer", "channel-123")


@pytest.mark.parametrize(
    "url", ["https://www.twitch.tv/videos/123", "https://clips.twitch.tv/clip123"]
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

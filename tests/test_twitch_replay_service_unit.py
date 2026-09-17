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


@pytest.fixture
def replay_request():
    return ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )


def test_replay_service_get_chat_by_vod_id_raises_when_video_missing(downloader):
    downloader._download_gql.return_value = [{"data": {"video": None}}]

    with pytest.raises(VideoUnavailable):
        replay_service.get_chat_by_vod_id(
            cast("Any", downloader),
            "vod123",
            ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=1),
        )

    downloader._update_badge_info.assert_not_called()


def test_replay_service_get_chat_by_vod_id_allows_missing_owner_login(downloader):
    downloader._download_gql.return_value = [
        {
            "data": {
                "video": {"title": "Replay", "lengthSeconds": 12.5, "owner": {}},
            },
        },
    ]
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


def test_replay_service_iter_vod_chat_messages_retries_then_stops_on_empty_page(
    downloader,
) -> None:
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


def test_mobile_replay_fallback_drives_full_multi_page_composition() -> None:
    calls: list[list[dict[str, Any]]] = []

    def mobile_page(message_id: str, cursor: str, offset: int) -> list[dict[str, Any]]:
        return [
            {
                "data": {
                    "video": {
                        "comments": {
                            "edges": [
                                {
                                    "cursor": cursor,
                                    "node": {
                                        "__typename": "VideoComment",
                                        "id": message_id,
                                        "createdAt": "2026-08-31T00:00:00Z",
                                        "contentOffsetSeconds": offset,
                                        "commenter": {
                                            "id": "user-1",
                                            "login": "viewer",
                                            "displayName": "Viewer",
                                        },
                                        "message": {
                                            "fragments": [
                                                {
                                                    "text": "Kappa",
                                                    "emote": {
                                                        "from": 0,
                                                        "emoteID": "25",
                                                        "to": 4,
                                                    },
                                                }
                                            ],
                                            "userBadges": [
                                                {
                                                    "setID": "subscriber",
                                                    "version": "1",
                                                }
                                            ],
                                        },
                                        "video": {
                                            "id": "vod-1",
                                            "owner": {"id": "owner-1"},
                                        },
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        ]

    responses: list[object] = [
        _PersistedQueryUnavailable("rotated"),
        mobile_page("message-1", "cursor-1", 1),
        _PersistedQueryUnavailable("rotated"),
        mobile_page("message-2", "", 2),
    ]

    def download(query: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append(query)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return cast("list[dict[str, Any]]", response)

    badge_set = BadgeSet(
        global_badges={},
        channel_badges={
            "owner-1": {
                ("subscriber", "1"): {
                    "title": "Channel subscriber",
                    "image1x": "https://example.invalid/1.png",
                    "image2x": "https://example.invalid/2.png",
                    "image4x": "https://example.invalid/4.png",
                }
            }
        },
    )
    downloader = SimpleNamespace(
        _session_post=Mock(),
        _download_gql=download,
        badge_cache=SimpleNamespace(snapshot=lambda: badge_set),
        retry=Mock(),
    )
    request = ChatRequest(
        url="https://www.twitch.tv/videos/123",
        max_attempts=1,
        message_groups=["messages"],
    )

    result = list(
        replay_service.iter_vod_chat_messages(
            cast("Any", downloader),
            "vod-1",
            request,
            max_duration=30,
            fetch_messages=get_chat_messages_by_vod_id,
        )
    )

    assert [item["message_id"] for item in result] == ["message-1", "message-2"]
    assert result[0]["emotes"][0]["locations"] == "0-4"
    assert result[0]["author"]["badges"][0]["title"] == "Channel subscriber"
    assert calls[2][0]["variables"] == {"videoID": "vod-1", "cursor": "cursor-1"}
    assert calls[3][0]["variables"] == {"vodId": "vod-1", "after": "cursor-1"}
    assert responses == []


def test_replay_service_iter_vod_chat_messages_handles_typenames_filters_and_stop(
    downloader,
) -> None:
    downloader.badge_cache.snapshot = lambda: {"badges": True}
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
        patch.object(
            _replay_vod_loop.MessageFilter,
            "from_request",
            return_value=fake_msg_filter,
        ),
        patch.object(replay_service, "_parse_item", side_effect=parsed_messages),
        patch.object(
            replay_service,
            "build_known_comment_keys",
            return_value={"message_type", "message_id"},
        ),
        patch.object(replay_service, "debug_log") as mock_debug_log,
        patch.object(replay_service.logger, "isEnabledFor", return_value=True),
        patch.object(
            replay_service,
            "capture_debug_sample",
        ) as mock_capture_debug_sample,
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
    assert mock_debug_log.call_count == 3
    assert mock_capture_debug_sample.call_count == 3
    mock_capture_debug_sample.assert_any_call(
        "twitch-unknown-gql-shape",
        {
            "raw": {
                "__typename": "VideoCommentEdge",
                "cursor": "c4",
                "node": {"__typename": "Comment", "id": "skip"},
            },
            "unexpected_output_keys": ["extra"],
            "parsed": parsed_messages[0],
        },
        sample_limit=10,
    )


def test_replay_service_iter_vod_chat_messages_logs_count_on_completed_page(
    downloader,
    replay_request,
) -> None:
    request = replay_request
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


@pytest.mark.parametrize("kind", ["vod", "clip"])
def test_replay_service_retries_metadata_before_success(downloader, kind):
    owner = {"id": "channel-123", "login": "streamer"}
    if kind == "vod":
        metadata = {
            "title": "Example VOD",
            "lengthSeconds": 123,
            "owner": owner,
        }
        response = [{"data": {"video": metadata}}]
        download = downloader._download_gql
        get_chat = replay_service.get_chat_by_vod_id
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
        download = downloader._download_base_gql
        get_chat = replay_service.get_chat_by_clip_id
        title = "Example Clip (123)"
    download.side_effect = [RequestException("temporary"), response]

    chat = get_chat(
        downloader,
        "123",
        ChatRequest(url="https://twitch.tv", max_attempts=2),
    )

    assert chat.title == title
    downloader.retry.assert_called_once()
    downloader._update_badge_info.assert_called_once_with("streamer", "channel-123")


def test_replay_service_get_chat_by_clip_id_raises_when_replay_missing(downloader):
    downloader._download_base_gql.return_value = {
        "data": {"clip": {"video": None, "title": "Expired Clip"}},
    }

    with pytest.raises(NoChatReplay):
        replay_service.get_chat_by_clip_id(
            cast("Any", downloader),
            "expired-clip",
            ChatRequest(url="https://clips.twitch.tv/expired-clip", max_attempts=1),
        )

    downloader._update_badge_info.assert_not_called()


@pytest.mark.parametrize(
    "url", ["https://www.twitch.tv/videos/123", "https://clips.twitch.tv/clip123"]
)
def test_replay_request_rejects_zero_attempts(url) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ChatRequest(url=url, max_attempts=0)


def test_iter_vod_stops_on_repeated_empty_pages_with_has_next_page(
    downloader,
    replay_request,
) -> None:
    """Pagination stops on empty edges even when hasNextPage=true."""
    request = replay_request
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


def test_iter_vod_stops_when_cursor_does_not_advance(
    downloader, replay_request
) -> None:
    """If a non-empty page returns the same cursor as before, stop."""
    request = replay_request
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


def test_iter_vod_chat_messages_skips_non_dict_edge_items(downloader, replay_request):
    request = replay_request
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

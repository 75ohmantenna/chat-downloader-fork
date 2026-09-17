# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from chat_downloader.sites.twitch import discovery, url_generation
from chat_downloader.sites.twitch.graphql_client import (
    _download_gql as _gql_download_gql,
)


def test_discovery_get_user_clips_remaps_clip_fields() -> None:
    calls = {}

    def download_gql_func(_session_post, query):
        calls["query"] = query
        return [
            {
                "data": {
                    "user": {
                        "clips": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "1",
                                        "slug": "clip-slug",
                                        "url": "https://clips.twitch.tv/clip-slug",
                                        "embedURL": "https://embed.example/clip-slug",
                                        "title": "Example Clip",
                                        "viewCount": 42,
                                        "language": "en",
                                        "curator": {"name": "curator"},
                                        "game": {"displayName": "Example Game"},
                                        "broadcaster": {"login": "streamer"},
                                        "thumbnailURL": "https://img.example/thumb.jpg",
                                        "createdAt": "2024-01-01T00:00:00Z",
                                        "durationSeconds": 18,
                                    },
                                },
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    },
                },
            },
        ]

    result = list(
        discovery.get_user_clips(
            session_post=lambda *args, **kwargs: None,
            download_gql_func=download_gql_func,
            username="streamer",
            limit=1,
        ),
    )

    assert calls["query"][0]["operationName"] == "ClipsCards__User"
    assert result == [
        {
            "id": "1",
            "slug": "clip-slug",
            "url": "https://clips.twitch.tv/clip-slug",
            "embed_url": "https://embed.example/clip-slug",
            "title": "Example Clip",
            "views": 42,
            "language": "en",
            "curator": {"name": "curator"},
            "game": {"display_name": "Example Game"},
            "broadcaster": {"name": "streamer"},
            "thumbnail_url": "https://img.example/thumb.jpg",
            "created_at": 1704067200000000,
            "duration": 18,
        },
    ]


def test_discovery_get_user_videos_paginates_with_cursor_and_skips_empty_nodes() -> (
    None
):
    calls: list[list[dict[str, Any]]] = []

    def download_gql_func(_session_post, query):
        calls.append(query)
        if len(calls) == 1:
            return [
                {
                    "data": {
                        "user": {
                            "id": "123",
                            "videos": {
                                "edges": [
                                    "not-a-dict",
                                    {"cursor": "cursor-1", "node": None},
                                    {
                                        "cursor": "cursor-2",
                                        "node": {
                                            "id": "7",
                                            "animatedPreviewURL": "anim-7",
                                            "game": {"name": "Game 7"},
                                            "lengthSeconds": 70,
                                            "owner": {"login": "streamer"},
                                            "previewThumbnailURL": "thumb-7",
                                            "publishedAt": "2024-01-07T00:00:00Z",
                                            "title": "Video 7",
                                            "viewCount": 700,
                                            "resourceRestriction": None,
                                        },
                                    },
                                ],
                                "pageInfo": {"hasNextPage": True},
                            },
                        },
                    },
                },
            ]
        return [
            {
                "data": {
                    "user": {
                        "id": "123",
                        "videos": {
                            "edges": [
                                {
                                    "cursor": "cursor-3",
                                    "node": {
                                        "id": "8",
                                        "animatedPreviewURL": "anim-8",
                                        "game": {"name": "Game 8"},
                                        "lengthSeconds": 80,
                                        "owner": {"login": "streamer"},
                                        "previewThumbnailURL": "thumb-8",
                                        "publishedAt": "2024-01-08T00:00:00Z",
                                        "title": "Video 8",
                                        "viewCount": 800,
                                        "resourceRestriction": "restricted",
                                    },
                                },
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    },
                },
            },
        ]

    result = list(
        discovery.get_user_videos(
            session_post=lambda *args, **kwargs: None,
            download_gql_func=download_gql_func,
            username="streamer",
            limit=4,
        ),
    )

    assert len(calls) == 2
    assert "cursor" not in calls[0][0]["variables"]
    assert calls[1][0]["variables"]["cursor"] == "cursor-2"
    assert [item["id"] for item in result] == ["7", "8"]
    assert result[1]["resource_restriction"] == "restricted"


@pytest.mark.parametrize(
    ("discover", "kwargs", "payload", "limit"),
    [
        (discovery.get_user_clips, {"username": "streamer"}, [], 0),
        (discovery.get_user_clips, {"username": "streamer"}, [], 5),
        (discovery.get_user_videos, {"username": "streamer"}, [], 0),
        (discovery.get_user_videos, {"username": "streamer"}, [], 5),
        (
            discovery.get_user_videos,
            {"username": "streamer"},
            [{"data": {"user": {"id": "123", "videos": None}}}],
            5,
        ),
        (discovery.get_top_livestreams, {}, [{"data": {"streams": {"edges": []}}}], 0),
        (discovery.get_top_livestreams, {}, [{"data": {"streams": {"edges": []}}}], 5),
    ],
)
def test_discovery_stops_for_zero_limit_or_empty_result(
    discover, kwargs, payload, limit
):
    download = Mock(return_value=payload)
    result = list(discover(Mock(), download, limit=limit, **kwargs))
    assert result == []
    assert download.call_count == (1 if limit else 0)
    if discover is discovery.get_top_livestreams and limit:
        assert download.call_args.args[1][0]["variables"]["limit"] == 5


def test_discovery_get_top_livestreams_logs_warning_when_streams_missing(
    caplog,
) -> None:
    def download_gql_func(_session_post, _query):
        return [{"data": {"streams": None}}]

    with caplog.at_level(logging.WARNING, logger="chat_downloader"):
        result = list(
            discovery.get_top_livestreams(
                session_post=lambda *args, **kwargs: None,
                download_gql_func=download_gql_func,
                limit=5,
            ),
        )

    assert result == []
    assert any(
        "Could not retrieve Twitch livestream data" in r.message for r in caplog.records
    )


def test_discovery_get_top_livestreams_paginates_and_remaps_none_nodes() -> None:
    calls: list[list[dict[str, Any]]] = []

    def download_gql_func(_session_post, query):
        calls.append(query)
        if len(calls) == 1:
            return [
                {
                    "data": {
                        "streams": {
                            "edges": [
                                {
                                    "cursor": "cursor-1",
                                    "node": {
                                        "id": "101",
                                        "title": "Top Stream",
                                        "viewersCount": 1000,
                                        "previewImageURL": "preview-101",
                                        "broadcaster": {"name": "one"},
                                        "game": {"name": "game-one"},
                                        "type": "live",
                                    },
                                },
                            ],
                        },
                    },
                },
            ]
        return [
            {
                "data": {
                    "streams": {
                        "edges": [
                            {
                                "cursor": "cursor-2",
                                "node": None,
                            },
                        ],
                    },
                },
            },
        ]

    result = list(
        discovery.get_top_livestreams(
            session_post=lambda *args, **kwargs: None,
            download_gql_func=download_gql_func,
            limit=31,
        ),
    )

    assert len(calls) == 2
    assert calls[0][0]["variables"]["cursor"] == ""
    assert calls[1][0]["variables"]["cursor"] == "cursor-1"
    assert calls[1][0]["variables"]["limit"] == 1
    assert result == [
        {
            "id": "101",
            "title": "Top Stream",
            "viewers": 1000,
            "preview_image_url": "preview-101",
            "broadcaster": {"name": "one"},
            "game": {"name": "game-one"},
            "type": "live",
        },
        {},
    ]


def test_url_generation_builds_stream_vod_and_clip_urls() -> None:
    downloader = SimpleNamespace(_session_post=Mock(), _download_gql=Mock())

    with (
        patch.object(
            url_generation,
            "get_top_livestreams",
            return_value=[
                {"broadcaster": {"name": "streamer1"}},
                {"broadcaster": {"name": "streamer2"}},
            ],
        ) as mock_streams,
        patch.object(
            url_generation,
            "get_user_videos",
            side_effect=[
                [{"id": "vod1"}, {"id": None}],
                [{"id": "vod2"}],
            ],
        ) as mock_videos,
        patch.object(
            url_generation,
            "get_user_clips",
            side_effect=[
                [{"url": "https://clips.twitch.tv/clip1"}, {"url": None}],
                [{"url": "https://clips.twitch.tv/clip2"}],
            ],
        ) as mock_clips,
        patch.object(url_generation, "_parse_user", side_effect=lambda value: value),
    ):
        result = list(url_generation.generate_urls(cast("Any", downloader), 2, 3, 4))

    assert result == [
        "https://www.twitch.tv/streamer1",
        "https://www.twitch.tv/videos/vod1",
        "https://clips.twitch.tv/clip1",
        "https://www.twitch.tv/streamer2",
        "https://www.twitch.tv/videos/vod2",
        "https://clips.twitch.tv/clip2",
    ]
    mock_streams.assert_called_once_with(
        downloader._session_post,
        _gql_download_gql,
        2,
    )
    assert mock_videos.call_args_list[0].args[3] == 2
    assert mock_videos.call_args_list[1].args[3] == 2
    assert mock_clips.call_args_list[0].args[3] == 2
    assert mock_clips.call_args_list[1].args[3] == 2


def test_url_generation_uses_raw_limits_when_livestream_limit_is_zero() -> None:
    downloader = SimpleNamespace(_session_post=Mock(), _download_gql=Mock())

    with (
        patch.object(
            url_generation,
            "get_top_livestreams",
            return_value=[{"broadcaster": {"name": "streamer"}}],
        ),
        patch.object(url_generation, "get_user_videos", return_value=[]) as mock_videos,
        patch.object(url_generation, "get_user_clips", return_value=[]) as mock_clips,
        patch.object(url_generation, "_parse_user", side_effect=lambda value: value),
    ):
        result = list(url_generation.generate_urls(cast("Any", downloader), 0, 5, 7))

    assert result == ["https://www.twitch.tv/streamer"]
    assert mock_videos.call_args.args[3] == 5
    assert mock_clips.call_args.args[3] == 7


def test_url_generation_skips_livestreams_without_broadcaster_name() -> None:
    downloader = SimpleNamespace(_session_post=Mock(), _download_gql=Mock())

    with (
        patch.object(
            url_generation,
            "get_top_livestreams",
            return_value=[
                {"broadcaster": {}},
                {"broadcaster": {"name": "kept"}},
            ],
        ),
        patch.object(url_generation, "get_user_videos", return_value=[]),
        patch.object(url_generation, "get_user_clips", return_value=[]),
        patch.object(url_generation, "_parse_user", side_effect=lambda value: value),
    ):
        result = list(url_generation.generate_urls(cast("Any", downloader), 2, 1, 1))

    assert result == ["https://www.twitch.tv/kept"]

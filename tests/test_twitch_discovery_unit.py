# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chat_downloader.sites.twitch import discovery, url_generation
from chat_downloader.sites.twitch.graphql_client import _download_gql


def _page(kind, edges, more=False):
    connection = {"edges": edges, "pageInfo": {"hasNextPage": more}}
    data = (
        {"streams": connection}
        if kind == "streams"
        else {"user": {"id": "123", kind: connection}}
    )
    return [{"data": data}]


def _edge(node, cursor="cursor-1"):
    return {"cursor": cursor, "node": node}


def _video(number, restriction=None):
    return {
        "id": str(number),
        "animatedPreviewURL": f"anim-{number}",
        "game": {"name": f"Game {number}"},
        "lengthSeconds": number * 10,
        "owner": {"login": "streamer"},
        "previewThumbnailURL": f"thumb-{number}",
        "publishedAt": f"2024-01-0{number}T00:00:00Z",
        "title": f"Video {number}",
        "viewCount": number * 100,
        "resourceRestriction": restriction,
    }


def _mapped_fields(common, fields):
    return (
        {**common, **{source: value for source, _, value, _ in fields}},
        {**common, **{target: value for _, target, _, value in fields}},
    )


@pytest.mark.parametrize(
    ("kind", "node", "expected"),
    [
        (
            "clips",
            *_mapped_fields(
                {
                    "id": "1",
                    "slug": "clip-slug",
                    "title": "Example Clip",
                    "url": "https://clips.twitch.tv/clip-slug",
                    "language": "en",
                    "curator": {"name": "curator"},
                },
                [
                    (
                        "embedURL",
                        "embed_url",
                        "https://embed.example/clip-slug",
                        "https://embed.example/clip-slug",
                    ),
                    ("viewCount", "views", 42, 42),
                    (
                        "game",
                        "game",
                        {"displayName": "Example Game"},
                        {"display_name": "Example Game"},
                    ),
                    (
                        "broadcaster",
                        "broadcaster",
                        {"login": "streamer"},
                        {"name": "streamer"},
                    ),
                    (
                        "thumbnailURL",
                        "thumbnail_url",
                        "https://img.example/thumb.jpg",
                        "https://img.example/thumb.jpg",
                    ),
                    (
                        "createdAt",
                        "created_at",
                        "2024-01-01T00:00:00Z",
                        1704067200000000,
                    ),
                    ("durationSeconds", "duration", 18, 18),
                ],
            ),
        ),
        (
            "streams",
            *_mapped_fields(
                {
                    "id": "101",
                    "title": "Top Stream",
                    "type": "live",
                    "broadcaster": {"name": "one"},
                    "game": {"name": "game-one"},
                },
                [
                    ("viewersCount", "viewers", 1000, 1000),
                    (
                        "previewImageURL",
                        "preview_image_url",
                        "preview-101",
                        "preview-101",
                    ),
                ],
            ),
        ),
    ],
)
def test_discovery_remaps_fields_and_stream_pages(kind, node, expected):
    pages = [_page(kind, [_edge(node)])]
    if kind == "streams":
        pages.append(_page(kind, [_edge(None, "cursor-2")]))
    download = Mock(side_effect=pages)
    if kind == "clips":
        result = list(discovery.get_user_clips(Mock(), download, "streamer", limit=1))
        assert download.call_args.args[1][0]["operationName"] == "ClipsCards__User"
        assert result == [expected]
    else:
        result = list(discovery.get_top_livestreams(Mock(), download, limit=31))
        queries = [call.args[1][0]["variables"] for call in download.call_args_list]
        assert len(queries) == 2
        assert queries[0]["cursor"] == ""
        assert queries[1]["cursor"] == "cursor-1"
        assert queries[1]["limit"] == 1
        assert result == [expected, {}]


def test_discovery_get_user_videos_paginates_with_cursor_and_skips_empty_nodes():
    download = Mock(
        side_effect=[
            _page(
                "videos",
                ["not-a-dict", _edge(None), _edge(_video(7), "cursor-2")],
                True,
            ),
            _page("videos", [_edge(_video(8, "restricted"), "cursor-3")]),
        ]
    )
    result = list(discovery.get_user_videos(Mock(), download, "streamer", limit=4))
    queries = [call.args[1][0]["variables"] for call in download.call_args_list]
    assert len(queries) == 2
    assert "cursor" not in queries[0]
    assert queries[1]["cursor"] == "cursor-2"
    assert [item["id"] for item in result] == ["7", "8"]
    assert result[1]["resource_restriction"] == "restricted"


@pytest.mark.parametrize(
    ("kind", "payload", "limit"),
    [
        ("clips", [], 0),
        ("clips", [], 5),
        ("videos", [], 0),
        ("videos", [], 5),
        ("videos", [{"data": {"user": {"id": "123", "videos": None}}}], 5),
        ("streams", [{"data": {"streams": {"edges": []}}}], 0),
        ("streams", [{"data": {"streams": {"edges": []}}}], 5),
    ],
)
def test_discovery_stops_for_zero_limit_or_empty_result(kind, payload, limit):
    discover = (
        discovery.get_top_livestreams
        if kind == "streams"
        else getattr(discovery, f"get_user_{kind}")
    )
    kwargs = {} if kind == "streams" else {"username": "streamer"}
    download = Mock(return_value=payload)
    assert list(discover(Mock(), download, limit=limit, **kwargs)) == []
    assert download.call_count == (1 if limit else 0)
    if kind == "streams" and limit:
        assert download.call_args.args[1][0]["variables"]["limit"] == 5


def test_discovery_get_top_livestreams_logs_warning_when_streams_missing(caplog):
    download = Mock(return_value=[{"data": {"streams": None}}])
    with caplog.at_level(logging.WARNING, logger="chat_downloader"):
        assert list(discovery.get_top_livestreams(Mock(), download, limit=5)) == []
    assert any(
        "Could not retrieve Twitch livestream data" in r.message for r in caplog.records
    )


@pytest.mark.parametrize(
    ("limits", "broadcasters", "videos", "clips", "expected", "per_user"),
    [
        (
            (2, 3, 4),
            [{"name": "streamer1"}, {"name": "streamer2"}],
            [[{"id": "vod1"}, {"id": None}], [{"id": "vod2"}]],
            [
                [{"url": "https://clips.twitch.tv/clip1"}, {"url": None}],
                [{"url": "https://clips.twitch.tv/clip2"}],
            ],
            [
                "https://www.twitch.tv/streamer1",
                "https://www.twitch.tv/videos/vod1",
                "https://clips.twitch.tv/clip1",
                "https://www.twitch.tv/streamer2",
                "https://www.twitch.tv/videos/vod2",
                "https://clips.twitch.tv/clip2",
            ],
            (2, 2),
        ),
        (
            (0, 5, 7),
            [{"name": "streamer"}],
            [[]],
            [[]],
            ["https://www.twitch.tv/streamer"],
            (5, 7),
        ),
        (
            (2, 1, 1),
            [{}, {"name": "kept"}],
            [[]],
            [[]],
            ["https://www.twitch.tv/kept"],
            (1, 1),
        ),
    ],
)
def test_url_generation(
    monkeypatch, limits, broadcasters, videos, clips, expected, per_user
):
    downloader = SimpleNamespace(_session_post=Mock(), _download_gql=Mock())
    streams = Mock(return_value=[{"broadcaster": user} for user in broadcasters])
    mocks = [Mock(side_effect=pages) for pages in (videos, clips)]
    monkeypatch.setattr(url_generation, "get_top_livestreams", streams)
    monkeypatch.setattr(url_generation, "get_user_videos", mocks[0])
    monkeypatch.setattr(url_generation, "get_user_clips", mocks[1])
    monkeypatch.setattr(url_generation, "_parse_user", lambda value: value)
    assert list(url_generation.generate_urls(downloader, *limits)) == expected
    streams.assert_called_once_with(downloader._session_post, _download_gql, limits[0])
    for mock, pages, limit in zip(mocks, (videos, clips), per_user, strict=True):
        assert [call.args[3] for call in mock.call_args_list] == [limit] * len(pages)

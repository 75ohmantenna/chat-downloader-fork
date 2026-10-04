# SPDX-License-Identifier: MIT

"""Compose captured modern browse payloads with the public discovery methods."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader.sites.youtube import discovery, discovery_playlists
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.parsing.message_items_video import _parse_video
from tests.youtube_third_helpers import http_response, wrap

_FIXTURES = Path(__file__).parent / "fixtures" / "youtube" / "discovery"


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text())


def test_captured_shorts_reach_public_discovery(monkeypatch):
    initial = _fixture("shorts-modern.json")
    monkeypatch.setattr(
        discovery,
        "_get_initial_info",
        lambda *_: (
            initial,
            {"INNERTUBE_API_KEY": "fixture"},
            {},
        ),
    )
    with closing(YouTubeChatDownloader()) as site:
        videos = list(
            site.get_user_videos(
                handle="FreiGilsonSomdoMonteOFICIAL", video_type="shorts"
            )
        )
    assert [v["video_id"] for v in videos] == ["05fFq5uPMV0", "fk2oCDqq2nQ"]
    assert videos[0]["title"] == "You are the reason for my living"
    assert videos[0]["short_view_count"] == "106K views"
    assert videos[0]["video_type"] == "DEFAULT"


@pytest.mark.parametrize("bad_id", [None, 123, ""])
def test_malformed_short_is_skipped_before_valid_item(bad_id):
    good = _fixture("shorts-modern.json")["contents"]["twoColumnBrowseResultsRenderer"][
        "tabs"
    ][0]["tabRenderer"]["content"]["richGridRenderer"]["contents"][0]
    bad = deepcopy(good)
    bad["richItemRenderer"]["content"]["shortsLockupViewModel"]["onTap"][
        "innertubeCommand"
    ]["reelWatchEndpoint"]["videoId"] = bad_id
    videos, token = discovery._process_page_items([bad, good])
    assert [v["video_id"] for v in videos] == ["05fFq5uPMV0"]
    assert token is None


@pytest.mark.parametrize("name", ["uploads-modern.json", "real-playlist-modern.json"])
def test_modern_playlist_initial_items_and_pagination(monkeypatch, name):
    initial = _fixture(name)
    items = initial["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0][
        "tabRenderer"
    ]["content"]["sectionListRenderer"]["contents"][0]["itemSectionRenderer"][
        "contents"
    ]
    expected = [
        x["lockupViewModel"]["contentId"] for x in items if "lockupViewModel" in x
    ]
    monkeypatch.setattr(
        discovery_playlists,
        "_get_initial_info",
        lambda *_: (
            initial,
            {"INNERTUBE_API_KEY": "fixture"},
            {},
        ),
    )
    post = Mock(
        return_value=http_response(
            payload={
                "onResponseReceivedActions": [
                    wrap(
                        "appendContinuationItemsAction.continuationItems",
                        [
                            {
                                "lockupViewModel": {
                                    "contentId": "next-video",
                                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                                }
                            },
                            wrap(
                                "continuationItemViewModel.continuationCommand."
                                "innertubeCommand.continuationCommand.token",
                                "fixture-next-page",
                            ),
                        ],
                    )
                ],
            }
        )
    )
    with closing(YouTubeChatDownloader()) as site:
        monkeypatch.setattr(site, "_session_post", post)
        videos = list(
            site.get_playlist_items("https://www.youtube.com/playlist?list=fixture")
        )
    paginated = name == "uploads-modern.json"
    assert [v["video_id"] for v in videos] == [
        *expected,
        *(["next-video"] if paginated else []),
    ]
    assert videos[0]["title"]
    assert videos[0]["view_count"] == ("23K" if paginated else "1.4M")
    assert post.call_count == int(paginated)
    if paginated:
        assert post.call_args.kwargs["json"]["continuation"] == "fixture-next-page"


def test_playlist_ignores_unselected_tabs_and_nonvideo_lockups(monkeypatch):
    initial = _fixture("uploads-modern.json")
    tabs = initial["contents"]["twoColumnBrowseResultsRenderer"]["tabs"]
    unrelated = deepcopy(tabs[0])
    unrelated["tabRenderer"]["selected"] = False
    tabs.insert(0, unrelated)
    items = tabs[1]["tabRenderer"]["content"]["sectionListRenderer"]["contents"][0][
        "itemSectionRenderer"
    ]["contents"]
    items[:] = [
        {
            "lockupViewModel": {
                "contentId": "PL-other",
                "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST",
            }
        },
        items[0],
    ]
    monkeypatch.setattr(
        discovery_playlists,
        "_get_initial_info",
        lambda *_: (
            initial,
            {"INNERTUBE_API_KEY": "fixture"},
            {},
        ),
    )
    with closing(YouTubeChatDownloader()) as site:
        videos = list(
            site.get_playlist_items("https://www.youtube.com/playlist?list=fixture")
        )
    assert [v["video_id"] for v in videos] == ["_1xR5zVKwUE"]


@pytest.mark.parametrize("tabs", [None, [], [None], [{"tabRenderer": {}}]])
def test_missing_playlist_content_is_empty(tabs):
    assert discovery_playlists._extract_playlist_items(
        discovery_playlists._get_playlist_initial_items(
            wrap("contents.twoColumnBrowseResultsRenderer.tabs", tabs)
        )
    ) == ([], None)


def test_legacy_playlist_sections_are_flattened_without_recursing_into_menus():
    content = wrap(
        "sectionListRenderer.contents",
        [
            "malformed",
            wrap(
                "itemSectionRenderer.contents",
                [
                    {"menu": wrap("playlistVideoRenderer.videoId", "unrelated")},
                    wrap(
                        "playlistVideoListRenderer.contents",
                        [
                            None,
                            wrap("playlistVideoRenderer.videoId", "legacy-video"),
                        ],
                    ),
                ],
            ),
        ],
    )
    initial = wrap(
        "contents.twoColumnBrowseResultsRenderer.tabs",
        [
            {"tabRenderer": {"content": content}},
        ],
    )
    videos, token = discovery_playlists._extract_playlist_items(
        discovery_playlists._get_playlist_initial_items(initial)
    )
    assert [v["video_id"] for v in videos] == ["legacy-video"]
    assert token is None


@pytest.mark.parametrize("bad_id", [None, ""])
def test_playlist_skips_video_lockup_without_id(bad_id):
    videos, token = discovery_playlists._extract_playlist_items(
        [
            {
                "lockupViewModel": {
                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                    "contentId": bad_id,
                }
            },
        ]
    )
    assert videos == []
    assert token is None


@pytest.mark.parametrize("playlist", [False, True])
def test_populated_unknown_models_emit_bounded_drift_evidence(monkeypatch, playlist):
    capture = Mock()
    debug = Mock()
    monkeypatch.setattr(discovery, "capture_debug_sample", capture)
    monkeypatch.setattr(discovery, "debug_log", debug)
    items = [{"newVideoViewModel": {"id": str(i)}} for i in range(12)]
    parser = (
        discovery_playlists._extract_playlist_items
        if playlist
        else discovery._process_page_items
    )
    assert parser(items) == ([], None)
    assert debug.call_count == 1
    assert capture.call_args.args[0] == "youtube-unsupported-discovery-items"
    sample = capture.call_args.args[1]
    assert sample["count"] == 12
    assert len(sample["items"]) == 10


@pytest.mark.parametrize("text", ["Fixture channel", "Streamed 3h ago", "3h ago"])
def test_lockup_does_not_treat_unlabeled_author_or_date_as_view_count(text):
    lockup = wrap(
        "metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel.metadataRows",
        [
            {"metadataParts": [wrap("text.content", text)]},
        ],
    )
    assert "view_count" not in _parse_video({"lockupViewModel": lockup})


@pytest.mark.parametrize("text", ["23K", "1.4M", "123", "1 watching"])
def test_legacy_unlabeled_view_counts_remain_supported(text):
    lockup = wrap(
        "metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel.metadataRows",
        [
            {"metadataParts": [wrap("text.content", text)]},
        ],
    )
    assert _parse_video({"lockupViewModel": lockup})["view_count"] == text

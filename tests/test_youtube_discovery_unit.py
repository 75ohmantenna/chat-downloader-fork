# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chat_downloader.errors import InvalidParameter, NoVideos, UserNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube import discovery, discovery_playlists, helpers
from chat_downloader.sites.youtube.chat_users_router import YouTubeChatUsersRouterMixin
from chat_downloader.sites.youtube.discovery import YouTubeDiscoveryMixin
from chat_downloader.sites.youtube.discovery_playlists import (
    YouTubePlaylistDiscoveryMixin,
)
from tests.youtube_third_helpers import returns, wrap

PLAYLIST = "https://www.youtube.com/playlist?list=PL123"


def _browse(tabs):
    return wrap("contents.twoColumnBrowseResultsRenderer.tabs", tabs)


def _tab(content, title="Videos", selected=True):
    return {"tabRenderer": {"selected": selected, "title": title, "content": content}}


def _rich_video(video_id):
    return wrap("richItemRenderer.content.videoRenderer.videoId", video_id)


def _continuation(token):
    return wrap(
        "continuationItemRenderer.continuationEndpoint.continuationCommand.token", token
    )


def _shelf(url):
    return wrap("shelfRenderer.endpoint.commandMetadata.webCommandMetadata.url", url)


def _section(items):
    return wrap(
        "sectionListRenderer.contents", [wrap("itemSectionRenderer.contents", items)]
    )


def _rich_section(content):
    return wrap(
        "richGridRenderer.contents", [wrap("richSectionRenderer.content", content)]
    )


def _append(items, key="onResponseReceivedActions"):
    return {key: [wrap("appendContinuationItemsAction.continuationItems", items)]}


def _playlist_response(token):
    return {
        **_append([]),
        **wrap(
            "continuationContents.playlistVideoListContinuation.contents",
            [_continuation(token)],
        ),
    }


def _patch_browse(monkeypatch, tabs, *, ytcfg=None, context=None):
    returns(
        monkeypatch,
        "discovery._get_initial_info",
        (_browse(tabs), {"INNERTUBE_API_KEY": "key"} if ytcfg is None else ytcfg, {}),
    )
    if context is not None:
        returns(monkeypatch, "discovery._get_innertube_context", context)


def _patch_playlist(monkeypatch, items, *, parse=False):
    returns(
        monkeypatch,
        "discovery_playlists._get_rendered_content",
        wrap("playlistVideoListRenderer.contents", items),
    )
    returns(
        monkeypatch,
        "discovery_playlists._get_initial_info",
        ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    returns(
        monkeypatch,
        "discovery_playlists._get_innertube_context",
        {"client": {"visitorData": "visitor"}},
    )
    if parse:
        monkeypatch.setattr(
            discovery_playlists, "_parse_video", lambda v: {"video_id": v["videoId"]}
        )


@pytest.fixture(autouse=True)
def _disable_browse_cookie_auth(monkeypatch):
    returns(monkeypatch, "helpers._generate_sapisidhash_header", None)


class _Discovery(YouTubeDiscoveryMixin):
    _session_get = object()
    _session_post = object()

    @staticmethod
    def _coerce_chat_request(params):
        return (
            params
            if isinstance(params, ChatRequest)
            else ChatRequest.from_kwargs(**params)
        )


class _Playlist(YouTubePlaylistDiscoveryMixin):
    _session_get = object()
    _session_post = object()


@pytest.mark.parametrize(
    ("selector", "kind"),
    [
        ("channel/", "channel_id"),
        ("user/", "user_id"),
        ("c/", "custom_username"),
        (None, "custom_username"),
        ("@/", "handle"),
        ("unsupported/", None),
    ],
)
def test_user_router_dispatch(selector, kind):
    router = YouTubeChatUsersRouterMixin()
    request = ChatRequest(url="https://www.youtube.com/@example/live")
    match = SimpleNamespace(group=lambda name: "abc" if name == "id" else selector)
    if kind is None:
        with pytest.raises(ValueError, match="Invalid user_type"):
            router._get_chat_by_user(match, request)
    else:
        handler = Mock(return_value=(kind, "abc", request))
        setattr(router, f"get_chat_by_{kind}", handler)
        assert router._get_chat_by_user(match, request) == (kind, "abc", request)
        handler.assert_called_once_with("abc", request)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "No user type specified"),
        ({"channel_id": "abc", "video_type": "unknown"}, "Invalid argument"),
        *[
            ({"channel_id": "abc", "video_type": v}, "non-empty string")
            for v in ("", None, 123)
        ],
        ({"handle": "@"}, "Invalid YouTube handle"),
    ],
)
def test_invalid_discovery_selectors(kwargs, match):
    with pytest.raises(InvalidParameter, match=match):
        list(YouTubeDiscoveryMixin.get_user_videos(object(), **kwargs))


@pytest.mark.parametrize(
    "params", [None, {"url": "https://www.youtube.com/channel/abc"}]
)
def test_discovery_coerces_only_supplied_params(monkeypatch, params):
    owner = _Discovery()
    owner._coerce_chat_request = Mock(side_effect=lambda value: ChatRequest(**value))
    _patch_browse(monkeypatch, [_tab({})])
    assert list(owner.get_user_videos(channel_id="abc", params=params)) == []
    if params is None:
        owner._coerce_chat_request.assert_not_called()
    else:
        owner._coerce_chat_request.assert_called_once_with(params)


@pytest.mark.parametrize(
    ("tabs", "error", "match"),
    [
        ([], UserNotFound, "Unable to find user"),
        ([_tab({}, "Streams")], NoVideos, "has no videos of the requested type"),
    ],
)
def test_missing_requested_tab(monkeypatch, tabs, error, match):
    _patch_browse(monkeypatch, tabs, ytcfg={})
    with pytest.raises(error, match=match):
        list(_Discovery().get_user_videos(channel_id="abc"))


def test_tab_selection_skips_malformed_entries():
    content = wrap("richGridRenderer.contents", [])
    assert (
        discovery._select_videos_tab(
            _browse(["malformed", None, _tab(content)]), "channel", "videos"
        )
        == content
    )


@pytest.mark.parametrize("playlist", [False, True])
@pytest.mark.parametrize(
    ("prefix", "video_id"),
    [
        (
            [wrap("continuationItemRenderer.continuationEndpoint", {})],
            "after-empty-token",
        ),
        (["malformed", None, {"unknownRenderer": {}}], "after-unknown"),
    ],
)
def test_item_processing_skips_invalid_entries(monkeypatch, playlist, prefix, video_id):
    module = discovery_playlists if playlist else discovery
    monkeypatch.setattr(module, "_parse_video", lambda v: {"video_id": v["videoId"]})
    process = module._extract_playlist_items if playlist else module._process_page_items
    item = (
        wrap("playlistVideoRenderer.videoId", video_id)
        if playlist
        else _rich_video(video_id)
    )
    assert process([*prefix, item]) == ([{"video_id": video_id}], None)


def test_no_selected_tab_content(monkeypatch):
    _patch_browse(
        monkeypatch,
        [
            _tab(wrap("richGridRenderer.contents", []), title, False)
            for title in ("Videos", "Shorts")
        ],
    )
    assert list(_Discovery().get_user_videos(channel_id="abc")) == []


def test_initial_page_and_continuation(monkeypatch):
    request = ChatRequest(url="https://www.youtube.com/channel/abc/videos")
    _patch_browse(
        monkeypatch,
        [
            _tab(
                wrap(
                    "richGridRenderer.contents",
                    [
                        _rich_video("one"),
                        wrap(
                            "richItemRenderer.content.lockupViewModel.contentId",
                            "lockup-one",
                        ),
                        _continuation("cont-1"),
                    ],
                )
            )
        ],
        context={"client": {"visitorData": "visitor"}},
    )
    fetch = Mock(return_value=_append([_rich_video("two")]))
    monkeypatch.setattr(helpers, "_get_continuation_info", fetch)
    monkeypatch.setattr(
        discovery,
        "_parse_video",
        lambda v: {"video_id": v.get("videoId") or v["lockupViewModel"]["contentId"]},
    )
    assert list(_Discovery().get_user_videos(channel_id="abc", params=request)) == [
        {"video_id": v} for v in ("one", "lockup-one", "two")
    ]
    assert fetch.call_args.args[2] is request
    assert fetch.call_args.kwargs["json"]["continuation"] == "cont-1"


def test_playlist_follows_continuation_only_response(monkeypatch):
    request = ChatRequest(url=PLAYLIST)
    _patch_playlist(
        monkeypatch,
        [wrap("playlistVideoRenderer.videoId", "one"), _continuation("cont-1")],
        parse=True,
    )
    calls = []
    responses = iter(
        [
            _playlist_response("cont-2"),
            _append(
                [wrap("playlistVideoRenderer.videoId", "two")],
                "onResponseReceivedEndpoints",
            ),
        ]
    )

    def fetch(_url, _post, params, **kwargs):
        calls.append((params, kwargs["json"]["continuation"]))
        return next(responses)

    monkeypatch.setattr(helpers, "_get_continuation_info", fetch)
    assert list(_Playlist().get_playlist_items(PLAYLIST, request)) == [
        {"video_id": "one"},
        {"video_id": "two"},
    ]
    assert calls == [(request, "cont-1"), (request, "cont-2")]


@pytest.mark.parametrize(
    ("content", "playlist_id", "video_id"),
    [
        (_section([_shelf("/playlist?list=PL123")]), "PL123", "abc123"),
        (_rich_section(_shelf("/playlist?list=PL999")), "PL999", "xyz789"),
    ],
)
def test_testing_items_finds_playlists(monkeypatch, content, playlist_id, video_id):
    owner = _Discovery()
    owner.get_playlist_items = Mock(return_value=[{"video_id": video_id}])
    _patch_browse(monkeypatch, [_tab(content)], ytcfg={})
    assert list(owner._get_testing_items()) == [{"video_id": video_id}]
    owner.get_playlist_items.assert_called_once_with(
        f"https://www.youtube.com/playlist?list={playlist_id}"
    )


def test_testing_items_deduplicates_direct_videos(monkeypatch):
    owner = _Discovery()
    owner.get_playlist_items = Mock(side_effect=AssertionError("unexpected playlist"))
    content = _rich_section(
        wrap(
            "richShelfRenderer.contents",
            [_rich_video(v) for v in ("one", "two", "one")],
        )
    )
    _patch_browse(monkeypatch, [_tab(content)], ytcfg={})
    assert list(owner._get_testing_items()) == [
        {"video_id": "one"},
        {"video_id": "two"},
    ]


def test_rendered_content():
    assert discovery._get_rendered_content(
        _browse([_tab(_section([{"target": "value"}]))])
    ) == {"target": "value"}


@pytest.mark.parametrize("params", [{"url": PLAYLIST}, None])
def test_playlist_stops_on_empty_continuation(monkeypatch, params):
    _patch_playlist(
        monkeypatch,
        [wrap("playlistVideoRenderer.videoId", "one"), _continuation("cont-1")],
        parse=True,
    )
    fetch = Mock(return_value=_append([]))
    monkeypatch.setattr(helpers, "_get_continuation_info", fetch)
    assert list(_Playlist().get_playlist_items(PLAYLIST, params)) == [
        {"video_id": "one"}
    ]
    assert isinstance(fetch.call_args.args[2], ChatRequest)
    assert fetch.call_count == 1
    assert fetch.call_args.kwargs["json"]["continuation"] == "cont-1"


@pytest.mark.parametrize("repeat", [False, True])
def test_playlist_without_items_or_repeated_token(monkeypatch, repeat):
    _patch_playlist(monkeypatch, [_continuation("loop")] if repeat else [])
    returns(monkeypatch, "helpers._get_continuation_info", _playlist_response("loop"))
    params = {"url": PLAYLIST} if repeat else None
    assert list(_Playlist().get_playlist_items(PLAYLIST, params)) == []


@pytest.mark.parametrize(
    ("selector", "value", "route"),
    [
        ("user_id", "user123", "user/user123"),
        ("custom_username", "creator", "c/creator"),
        ("handle", "name", "@name"),
        ("handle", "@name", "@name"),
    ],
)
def test_non_channel_selectors(monkeypatch, selector, value, route):
    fetch = Mock(
        return_value=(
            _browse(
                [
                    {"tabRenderer": {"selected": False, "title": "Home"}},
                    _tab(wrap("richGridRenderer.contents", [])),
                ]
            ),
            {"INNERTUBE_API_KEY": "key"},
            {},
        )
    )
    monkeypatch.setattr(discovery, "_get_initial_info", fetch)
    returns(monkeypatch, "discovery._get_innertube_context", {"client": {}})
    assert list(_Discovery().get_user_videos(**{selector: value})) == []
    assert fetch.call_args.args[0] == f"https://www.youtube.com/{route}/videos"


def test_discovery_breaks_continuation_loop(monkeypatch):
    fetch = Mock(side_effect=[{"onResponseReceivedActions": [{}]}])
    monkeypatch.setattr(helpers, "_get_continuation_info", fetch)
    _patch_browse(
        monkeypatch,
        [_tab(wrap("richGridRenderer.contents", [_continuation("loop-token")]))],
        context={"client": {}},
    )
    returns(
        monkeypatch,
        "discovery._extract_browse_continuation_token_from_response",
        "loop-token",
    )
    assert list(_Discovery().get_user_videos(channel_id="abc")) == []
    assert fetch.call_count == 1


def test_generate_watch_urls():
    owner = _Discovery()
    owner._get_testing_items = Mock(
        return_value=[{"video_id": "abc123"}, {"video_id": "def456"}]
    )
    assert list(owner.generate_urls()) == [
        f"https://www.youtube.com/watch?v={v}" for v in ("abc123", "def456")
    ]


def test_continuation_view_models():
    item = wrap(
        "continuationItemViewModel.continuationCommand.innertubeCommand."
        "continuationCommand.token",
        "view-model-token",
    )
    for process in (
        discovery._process_page_items,
        discovery_playlists._extract_playlist_items,
    ):
        assert process([item]) == ([], "view-model-token")
    assert helpers._extract_browse_continuation_token_from_item("malformed") is None


def test_discovery_composed_on_downloader(monkeypatch):
    from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader

    _patch_browse(
        monkeypatch,
        [_tab(wrap("richGridRenderer.contents", [_rich_video("assembled")]))],
    )
    monkeypatch.setattr(discovery, "_parse_video", lambda v: {"video_id": v["videoId"]})
    assert list(
        object.__new__(YouTubeChatDownloader).get_user_videos(channel_id="channel-id")
    ) == [{"video_id": "assembled"}]


def test_recursion_past_nonmatching_entries():
    content = {
        **_shelf("/watch?v=not-playlist"),
        "nested": [
            wrap("videoRenderer.videoId", ""),
            _shelf("/playlist?list=PL123"),
            wrap("videoRenderer.videoId", "abc123"),
        ],
    }
    assert list(discovery._iter_playlist_urls(content)) == [PLAYLIST]
    assert list(discovery._iter_video_ids(content)) == ["abc123"]


def test_empty_token_does_not_mark_seen_and_refreshes_visitor(monkeypatch):
    calls, configs = [], []

    def fetch(*_args, **kwargs):
        calls.append((kwargs["json"], kwargs["headers"]))
        return {
            "responseContext": {"visitorData": "visitor-2"},
            **_append([wrap("richItemRenderer.content", {})]),
        }

    monkeypatch.setattr(helpers, "_get_continuation_info", fetch)
    monkeypatch.setattr(
        helpers,
        "_generate_headers",
        lambda config, *_: configs.append(config) or {"X-Test": "api"},
    )
    seen = set()
    params = {"context": {"client": {"visitorData": "visitor-1"}}}
    items, info = helpers._fetch_browse_continuation(
        SimpleNamespace(_session_post=object()),
        "",
        "https://www.youtube.com/youtubei/v1/browse?key=key",
        params,
        {"INNERTUBE_CONTEXT": {"client": {"visitorData": "visitor-1"}}},
        ChatRequest(url="https://www.youtube.com/@example/videos"),
        seen,
    )
    assert items == [wrap("richItemRenderer.content", {})]
    assert info is not None
    assert calls == [
        (
            {"context": {"client": {"visitorData": "visitor-2"}}, "continuation": ""},
            {"X-Test": "api"},
        )
    ]
    assert configs[0]["INNERTUBE_CONTEXT"]["client"]["visitorData"] == "visitor-1"
    assert params["context"]["client"]["visitorData"] == "visitor-2"
    assert seen == set()

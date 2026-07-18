# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import InvalidParameter, NoVideos, UserNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.chat_users_router import (
    YouTubeChatUsersRouterMixin,
)
from chat_downloader.sites.youtube.discovery import (
    YouTubeDiscoveryMixin,
)
from chat_downloader.sites.youtube.discovery_playlists import (
    YouTubePlaylistDiscoveryMixin,
)


@pytest.fixture(autouse=True)
def _disable_browse_cookie_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )


class _DummyMatch:
    def __init__(self, match_id: str, match_type: str | None) -> None:
        self._groups = {"id": match_id, "type": match_type}

    def group(self, name: str) -> str | None:
        return self._groups[name]


def get_user_videos(owner, **kwargs):
    """Invoke the real mixin method on lightweight discovery test doubles."""
    return YouTubeDiscoveryMixin.get_user_videos(owner, **kwargs)


class _DummyDiscoveryBase(YouTubeDiscoveryMixin):
    @staticmethod
    def _coerce_chat_request(params):
        if isinstance(params, ChatRequest):
            return params
        return ChatRequest.from_kwargs(**params)


class _DummyUserRouter(YouTubeChatUsersRouterMixin):
    def get_chat_by_channel_id(self, match_id, params):
        return ("channel", match_id, params)

    def get_chat_by_user_id(self, match_id, params):
        return ("user", match_id, params)

    def get_chat_by_custom_username(self, match_id, params):
        return ("custom", match_id, params)

    def get_chat_by_handle(self, match_id, params):
        return ("handle", match_id, params)


def test_user_router_dispatches_supported_user_types() -> None:
    router = _DummyUserRouter()
    request = ChatRequest(url="https://www.youtube.com/@example/live")

    assert router._get_chat_by_user(_DummyMatch("abc", "channel/"), request) == (
        "channel",
        "abc",
        request,
    )
    assert router._get_chat_by_user(_DummyMatch("abc", "user/"), request) == (
        "user",
        "abc",
        request,
    )
    assert router._get_chat_by_user(_DummyMatch("abc", "c/"), request) == (
        "custom",
        "abc",
        request,
    )
    assert router._get_chat_by_user(_DummyMatch("abc", None), request) == (
        "custom",
        "abc",
        request,
    )
    assert router._get_chat_by_user(_DummyMatch("abc", "@/"), request) == (
        "handle",
        "abc",
        request,
    )


def test_user_router_rejects_unknown_user_type() -> None:
    router = _DummyUserRouter()

    with pytest.raises(ValueError, match="Invalid user_type"):
        router._get_chat_by_user(
            _DummyMatch("abc", "unsupported/"),
            ChatRequest(url="https://www.youtube.com/unsupported/abc"),
        )


def test_get_user_videos_requires_user_selector() -> None:
    with pytest.raises(InvalidParameter, match="No user type specified"):
        list(get_user_videos(object()))


def test_channel_discovery_mixin_coerces_dict_params(monkeypatch) -> None:
    captured = []

    class DummyDiscovery(YouTubeDiscoveryMixin):
        _session_get = object()
        _session_post = object()

        def _coerce_chat_request(self, params):
            request = ChatRequest(**params)
            captured.append(request)
            return request

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {},
                                }
                            }
                        ]
                    }
                }
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )

    result = list(
        DummyDiscovery().get_user_videos(
            channel_id="abc",
            params={"url": "https://www.youtube.com/channel/abc/videos"},
        ),
    )

    assert result == []
    assert len(captured) == 1
    assert isinstance(captured[0], ChatRequest)


def test_channel_discovery_mixin_passes_none_params_without_coercion(
    monkeypatch,
) -> None:
    class DummyDiscovery(YouTubeDiscoveryMixin):
        _session_get = object()
        _session_post = object()

        def _coerce_chat_request(self, params):
            raise AssertionError("should not coerce None")

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {},
                                }
                            }
                        ]
                    }
                }
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )

    assert list(DummyDiscovery().get_user_videos(channel_id="abc", params=None)) == []


def test_get_user_videos_rejects_invalid_video_type() -> None:
    with pytest.raises(
        InvalidParameter, match="Invalid argument passed for video_type"
    ):
        list(get_user_videos(object(), channel_id="abc", video_type="unknown"))


@pytest.mark.parametrize("video_type", ["", None, 123])
def test_get_user_videos_rejects_empty_or_non_string_video_type(video_type) -> None:
    with pytest.raises(InvalidParameter, match="non-empty string"):
        list(get_user_videos(object(), channel_id="abc", video_type=video_type))


def test_get_user_videos_rejects_empty_normalized_handle() -> None:
    with pytest.raises(InvalidParameter, match="Invalid YouTube handle"):
        list(get_user_videos(object(), handle="@"))


def test_get_user_videos_raises_user_not_found(monkeypatch) -> None:
    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {"contents": {"twoColumnBrowseResultsRenderer": {}}},
            {},
            {},
        ),
    )

    with pytest.raises(UserNotFound, match="Unable to find user"):
        list(get_user_videos(DummyDownloader(), channel_id="abc"))


def test_get_user_videos_raises_no_videos_when_selected_tab_mismatch(
    monkeypatch,
) -> None:
    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Streams",
                                    "content": {},
                                },
                            },
                        ],
                    },
                },
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )

    with pytest.raises(NoVideos, match="has no videos of the requested type"):
        list(get_user_videos(DummyDownloader(), channel_id="abc", video_type="videos"))


def test_tab_selection_skips_malformed_entries() -> None:
    from chat_downloader.sites.youtube.discovery import _select_videos_tab

    content = {"richGridRenderer": {"contents": []}}
    yt_info = {
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    "malformed",
                    None,
                    {
                        "tabRenderer": {
                            "selected": True,
                            "title": "Videos",
                            "content": content,
                        }
                    },
                ]
            }
        }
    }

    assert _select_videos_tab(yt_info, "channel", "videos") == content


def test_channel_page_item_processing_ignores_continuation_without_token(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import discovery
    from chat_downloader.sites.youtube.discovery import (
        _process_page_items,
    )

    monkeypatch.setattr(
        discovery,
        "_parse_video",
        lambda video: {"video_id": video["videoId"]},
    )

    videos, token = _process_page_items(
        [
            {"continuationItemRenderer": {"continuationEndpoint": {}}},
            {
                "richItemRenderer": {
                    "content": {"videoRenderer": {"videoId": "after-empty-token"}},
                },
            },
        ],
    )

    assert videos == [{"video_id": "after-empty-token"}]
    assert token is None


def test_channel_page_item_processing_skips_unknown_items(monkeypatch) -> None:
    from chat_downloader.sites.youtube import discovery
    from chat_downloader.sites.youtube.discovery import (
        _process_page_items,
    )

    monkeypatch.setattr(
        discovery,
        "_parse_video",
        lambda video: {"video_id": video["videoId"]},
    )

    videos, token = _process_page_items(
        [
            "malformed",
            None,
            {"unknownRenderer": {}},
            {
                "richItemRenderer": {
                    "content": {"videoRenderer": {"videoId": "after-unknown"}},
                },
            },
        ],
    )

    assert videos == [{"video_id": "after-unknown"}]
    assert token is None


def test_get_user_videos_returns_empty_when_no_selected_tab_has_content(
    monkeypatch,
) -> None:
    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": False,
                                    "title": "Videos",
                                    "content": {"richGridRenderer": {"contents": []}},
                                },
                            },
                            {
                                "tabRenderer": {
                                    "selected": False,
                                    "title": "Shorts",
                                    "content": {"richGridRenderer": {"contents": []}},
                                },
                            },
                        ],
                    },
                },
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )

    assert (
        list(get_user_videos(DummyDownloader(), channel_id="abc", video_type="videos"))
        == []
    )


def test_get_user_videos_yields_items_from_initial_page_and_continuation(
    monkeypatch,
) -> None:
    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    request = ChatRequest(url="https://www.youtube.com/channel/abc/videos")
    continuation_calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {
                                        "richGridRenderer": {
                                            "contents": [
                                                {
                                                    "richItemRenderer": {
                                                        "content": {
                                                            "videoRenderer": {
                                                                "videoId": "one",
                                                            },
                                                        },
                                                    },
                                                },
                                                {
                                                    "richItemRenderer": {
                                                        "content": {
                                                            "lockupViewModel": {
                                                                "contentId": "lockup-one",  # noqa: E501
                                                            },
                                                        },
                                                    },
                                                },
                                                {
                                                    "continuationItemRenderer": {
                                                        "continuationEndpoint": {
                                                            "continuationCommand": {
                                                                "token": "cont-1",
                                                            },
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )

    def fake_get_continuation_info(_url, _session_post, params, **kwargs):
        continuation_calls.append((params, kwargs["json"]["continuation"]))
        return {
            "onResponseReceivedActions": [
                {
                    "appendContinuationItemsAction": {
                        "continuationItems": [
                            {
                                "richItemRenderer": {
                                    "content": {"videoRenderer": {"videoId": "two"}},
                                },
                            },
                        ],
                    },
                },
            ],
        }

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        fake_get_continuation_info,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._parse_video",
        lambda video: {
            "video_id": video.get("videoId") or video["lockupViewModel"]["contentId"]
        },
    )

    videos = list(
        get_user_videos(
            DummyDownloader(),
            channel_id="abc",
            video_type="videos",
            params=request,
        ),
    )

    assert videos == [
        {"video_id": "one"},
        {"video_id": "lockup-one"},
        {"video_id": "two"},
    ]
    assert continuation_calls == [(request, "cont-1")]


def test_playlist_discovery_accepts_chat_request_and_follows_continuation_only_response(
    monkeypatch,
) -> None:
    class DummyPlaylistDiscovery(YouTubePlaylistDiscoveryMixin):
        _session_get = object()
        _session_post = object()

    request = ChatRequest(url="https://www.youtube.com/playlist?list=PL123")
    continuation_calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_rendered_content",
        lambda _yt_info, tab_index=0: {
            "playlistVideoListRenderer": {
                "contents": [
                    {"playlistVideoRenderer": {"videoId": "one"}},
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {"token": "cont-1"},
                            },
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_initial_info",
        lambda *_args, **_kwargs: ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._parse_video",
        lambda video: {"video_id": video["videoId"]},
    )

    responses = iter(
        [
            {
                "onResponseReceivedActions": [
                    {"appendContinuationItemsAction": {"continuationItems": []}},
                ],
                "continuationContents": {
                    "playlistVideoListContinuation": {
                        "contents": [
                            {
                                "continuationItemRenderer": {
                                    "continuationEndpoint": {
                                        "continuationCommand": {"token": "cont-2"},
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {
                "onResponseReceivedEndpoints": [
                    {
                        "appendContinuationItemsAction": {
                            "continuationItems": [
                                {"playlistVideoRenderer": {"videoId": "two"}},
                            ],
                        },
                    },
                ],
            },
        ],
    )

    def fake_get_continuation_info(_url, _session_post, params, **kwargs):
        continuation_calls.append((params, kwargs["json"]["continuation"]))
        return next(responses)

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        fake_get_continuation_info,
    )

    videos = list(
        DummyPlaylistDiscovery().get_playlist_items(
            "https://www.youtube.com/playlist?list=PL123",
            request,
        ),
    )

    assert videos == [{"video_id": "one"}, {"video_id": "two"}]
    assert continuation_calls == [(request, "cont-1"), (request, "cont-2")]


def test_get_testing_items_uses_live_playlist_and_delegates_playlist_loading(
    monkeypatch,
) -> None:
    class DummyDiscoveryHelpers(YouTubeDiscoveryMixin):
        _session_get = object()

        def __init__(self) -> None:
            self.playlist_urls = []

        def get_playlist_items(self, playlist_url):
            self.playlist_urls.append(playlist_url)
            yield {"video_id": "abc123"}

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "content": {
                                        "sectionListRenderer": {
                                            "contents": [
                                                {
                                                    "itemSectionRenderer": {
                                                        "contents": [
                                                            {
                                                                "shelfRenderer": {
                                                                    "endpoint": {
                                                                        "commandMetadata": {  # noqa: E501
                                                                            "webCommandMetadata": {  # noqa: E501
                                                                                "url": "/playlist?list=PL123",  # noqa: E501
                                                                            },
                                                                        },
                                                                    },
                                                                },
                                                            },
                                                        ],
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {},
            {},
        ),
    )

    helpers = DummyDiscoveryHelpers()

    assert list(helpers._get_testing_items()) == [{"video_id": "abc123"}]
    assert helpers.playlist_urls == ["https://www.youtube.com/playlist?list=PL123"]


def test_get_testing_items_finds_playlist_url_without_section_list_renderer(
    monkeypatch,
) -> None:
    class DummyDiscoveryHelpers(YouTubeDiscoveryMixin):
        _session_get = object()

        def __init__(self) -> None:
            self.playlist_urls = []

        def get_playlist_items(self, playlist_url):
            self.playlist_urls.append(playlist_url)
            yield {"video_id": "xyz789"}

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "content": {
                                        "richGridRenderer": {
                                            "contents": [
                                                {
                                                    "richSectionRenderer": {
                                                        "content": {
                                                            "shelfRenderer": {
                                                                "endpoint": {
                                                                    "commandMetadata": {
                                                                        "webCommandMetadata": {  # noqa: E501
                                                                            "url": "/playlist?list=PL999",  # noqa: E501
                                                                        },
                                                                    },
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {},
            {},
        ),
    )

    helpers = DummyDiscoveryHelpers()

    assert list(helpers._get_testing_items()) == [{"video_id": "xyz789"}]
    assert helpers.playlist_urls == ["https://www.youtube.com/playlist?list=PL999"]


def test_get_testing_items_yields_direct_video_renderers_from_rich_shelf(
    monkeypatch,
) -> None:
    class DummyDiscoveryHelpers(YouTubeDiscoveryMixin):
        _session_get = object()

        def get_playlist_items(self, playlist_url):
            raise AssertionError(f"unexpected playlist load: {playlist_url}")

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "content": {
                                        "richGridRenderer": {
                                            "contents": [
                                                {
                                                    "richSectionRenderer": {
                                                        "content": {
                                                            "richShelfRenderer": {
                                                                "contents": [
                                                                    {
                                                                        "richItemRenderer": {  # noqa: E501
                                                                            "content": {
                                                                                "videoRenderer": {  # noqa: E501
                                                                                    "videoId": "one",  # noqa: E501
                                                                                },
                                                                            },
                                                                        },
                                                                    },
                                                                    {
                                                                        "richItemRenderer": {  # noqa: E501
                                                                            "content": {
                                                                                "videoRenderer": {  # noqa: E501
                                                                                    "videoId": "two",  # noqa: E501
                                                                                },
                                                                            },
                                                                        },
                                                                    },
                                                                    {
                                                                        "richItemRenderer": {  # noqa: E501
                                                                            "content": {
                                                                                "videoRenderer": {  # noqa: E501
                                                                                    "videoId": "one",  # noqa: E501
                                                                                },
                                                                            },
                                                                        },
                                                                    },
                                                                ],
                                                            },
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {},
            {},
        ),
    )

    assert list(DummyDiscoveryHelpers()._get_testing_items()) == [
        {"video_id": "one"},
        {"video_id": "two"},
    ]


def test_get_rendered_content_extracts_selected_tab_content() -> None:
    from chat_downloader.sites.youtube.discovery import (
        _get_rendered_content,
    )

    rendered = _get_rendered_content(
        {
            "contents": {
                "twoColumnBrowseResultsRenderer": {
                    "tabs": [
                        {
                            "tabRenderer": {
                                "content": {
                                    "sectionListRenderer": {
                                        "contents": [
                                            {
                                                "itemSectionRenderer": {
                                                    "contents": [{"target": "value"}],
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        },
    )

    assert rendered == {"target": "value"}


def test_playlist_discovery_accepts_dict_params_and_stops_on_empty_continuation(
    monkeypatch,
) -> None:
    class DummyPlaylistDiscovery(YouTubePlaylistDiscoveryMixin):
        _session_get = object()
        _session_post = object()

    calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_rendered_content",
        lambda _yt_info, tab_index=0: {
            "playlistVideoListRenderer": {
                "contents": [
                    {"playlistVideoRenderer": {"videoId": "one"}},
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {"token": "cont-1"},
                            },
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_initial_info",
        lambda *_args, **_kwargs: ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._parse_video",
        lambda video: {"video_id": video["videoId"]},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        lambda _url, _session_post, request, **kwargs: (
            calls.append((request, kwargs["json"]["continuation"]))
            or {
                "onResponseReceivedActions": [
                    {"appendContinuationItemsAction": {"continuationItems": []}}
                ]
            }
        ),
    )

    videos = list(
        DummyPlaylistDiscovery().get_playlist_items(
            "https://www.youtube.com/playlist?list=PL123",
            {"url": "https://www.youtube.com/playlist?list=PL123"},
        ),
    )

    assert videos == [{"video_id": "one"}]
    assert isinstance(calls[0][0], ChatRequest)
    assert calls == [(calls[0][0], "cont-1")]


def test_playlist_discovery_accepts_none_params_without_continuation(
    monkeypatch,
) -> None:
    class DummyPlaylistDiscovery(YouTubePlaylistDiscoveryMixin):
        _session_get = object()
        _session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_rendered_content",
        lambda _yt_info, tab_index=0: {"playlistVideoListRenderer": {"contents": []}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_initial_info",
        lambda *_args, **_kwargs: ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )

    assert (
        list(
            DummyPlaylistDiscovery().get_playlist_items(
                "https://www.youtube.com/playlist?list=PL123",
                None,
            ),
        )
        == []
    )


def test_playlist_discovery_accepts_none_params_with_continuation(
    monkeypatch,
) -> None:
    class DummyPlaylistDiscovery(YouTubePlaylistDiscoveryMixin):
        _session_get = object()
        _session_post = object()

    calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_rendered_content",
        lambda _yt_info, tab_index=0: {
            "playlistVideoListRenderer": {
                "contents": [
                    {"playlistVideoRenderer": {"videoId": "one"}},
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {"token": "cont-1"},
                            },
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_initial_info",
        lambda *_args, **_kwargs: ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._parse_video",
        lambda video: {"video_id": video["videoId"]},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        lambda _url, _session_post, request, **kwargs: (
            calls.append((request, kwargs["json"]["continuation"]))
            or {
                "onResponseReceivedActions": [
                    {"appendContinuationItemsAction": {"continuationItems": []}}
                ]
            }
        ),
    )

    videos = list(
        DummyPlaylistDiscovery().get_playlist_items(
            "https://www.youtube.com/playlist?list=PL123",
            None,
        ),
    )

    assert videos == [{"video_id": "one"}]
    assert isinstance(calls[0][0], ChatRequest)
    assert calls == [(calls[0][0], "cont-1")]


def test_playlist_discovery_breaks_on_repeated_continuation(
    monkeypatch,
) -> None:
    class DummyPlaylistDiscovery(YouTubePlaylistDiscoveryMixin):
        _session_get = object()
        _session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_rendered_content",
        lambda _yt_info, tab_index=0: {
            "playlistVideoListRenderer": {
                "contents": [
                    {
                        "continuationItemRenderer": {
                            "continuationEndpoint": {
                                "continuationCommand": {"token": "loop"},
                            },
                        },
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_initial_info",
        lambda *_args, **_kwargs: ({}, {"INNERTUBE_API_KEY": "key"}, {}),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery_playlists._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        lambda *_args, **_kwargs: {
            "onResponseReceivedActions": [
                {"appendContinuationItemsAction": {"continuationItems": []}},
            ],
            "continuationContents": {
                "playlistVideoListContinuation": {
                    "contents": [
                        {
                            "continuationItemRenderer": {
                                "continuationEndpoint": {
                                    "continuationCommand": {"token": "loop"},
                                },
                            },
                        },
                    ],
                },
            },
        },
    )

    assert (
        list(
            DummyPlaylistDiscovery().get_playlist_items(
                "https://www.youtube.com/playlist?list=PL123",
                {"url": "https://www.youtube.com/playlist?list=PL123"},
            ),
        )
        == []
    )


def test_youtube_discovery_supports_non_channel_selectors(monkeypatch) -> None:
    seen_urls: list[str] = []

    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    def fake_initial_info(url, *_args, **_kwargs):
        seen_urls.append(url)
        return (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": False,
                                    "title": "Home",
                                }
                            },
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {"richGridRenderer": {"contents": []}},
                                },
                            },
                        ],
                    },
                },
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        )

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        fake_initial_info,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )

    assert list(get_user_videos(DummyDownloader(), user_id="user123")) == []
    assert list(get_user_videos(DummyDownloader(), custom_username="creator")) == []
    assert list(get_user_videos(DummyDownloader(), handle="name")) == []
    assert list(get_user_videos(DummyDownloader(), handle="@name")) == []
    assert seen_urls == [
        "https://www.youtube.com/user/user123/videos",
        "https://www.youtube.com/c/creator/videos",
        "https://www.youtube.com/@name/videos",
        "https://www.youtube.com/@name/videos",
    ]


def test_youtube_discovery_breaks_on_continuation_loop(monkeypatch) -> None:
    class DummyDownloader(_DummyDiscoveryBase):
        _session_get = object()
        _session_post = object()

    continuation_payloads = iter(
        [
            {"onResponseReceivedActions": [{}]},
        ]
    )
    logs: list[str] = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {
                                        "richGridRenderer": {
                                            "contents": [
                                                {
                                                    "continuationItemRenderer": {
                                                        "continuationEndpoint": {
                                                            "continuationCommand": {
                                                                "token": "loop-token",
                                                            },
                                                        },
                                                    },
                                                },
                                            ],
                                        },
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_innertube_context",
        lambda _ytcfg: {"client": {}},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers._get_continuation_info",
        lambda *_args, **_kwargs: next(continuation_payloads),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._extract_browse_continuation_token_from_response",
        lambda _yt_info: "loop-token",
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.helpers.log",
        lambda _level, message: logs.append(message),
    )

    assert list(get_user_videos(DummyDownloader(), channel_id="abc")) == []
    assert any("continuation loop" in message for message in logs)


def test_generate_urls_yields_watch_urls() -> None:
    from chat_downloader.sites.youtube.constants_patterns import _YT_HOME
    from chat_downloader.sites.youtube.discovery import (
        YouTubeDiscoveryMixin,
    )

    class _MockDiscovery(YouTubeDiscoveryMixin):
        def _get_testing_items(self):
            return [{"video_id": "abc123"}, {"video_id": "def456"}]

    obj = _MockDiscovery()
    urls = list(obj.generate_urls())
    assert urls == [
        f"{_YT_HOME}/watch?v=abc123",
        f"{_YT_HOME}/watch?v=def456",
    ]


def test_playlist_item_processing_ignores_continuation_without_token(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import discovery_playlists
    from chat_downloader.sites.youtube.discovery_playlists import (
        _extract_playlist_items,
    )

    monkeypatch.setattr(
        discovery_playlists,
        "_parse_video",
        lambda video: {"video_id": video["videoId"]},
    )

    videos, token = _extract_playlist_items(
        [
            {"continuationItemRenderer": {"continuationEndpoint": {}}},
            {"playlistVideoRenderer": {"videoId": "after-empty-token"}},
        ],
    )

    assert videos == [{"video_id": "after-empty-token"}]
    assert token is None


def test_discovery_item_processing_supports_continuation_view_models() -> None:
    from chat_downloader.sites.youtube.discovery import _process_page_items
    from chat_downloader.sites.youtube.discovery_playlists import (
        _extract_playlist_items,
    )
    from chat_downloader.sites.youtube.helpers import (
        _extract_browse_continuation_token_from_item,
    )

    continuation = {
        "continuationItemViewModel": {
            "continuationCommand": {
                "innertubeCommand": {
                    "continuationCommand": {"token": "view-model-token"},
                },
            },
        },
    }

    assert _process_page_items([continuation]) == ([], "view-model-token")
    assert _extract_playlist_items([continuation]) == ([], "view-model-token")
    assert _extract_browse_continuation_token_from_item("malformed") is None


def test_playlist_item_processing_skips_unknown_items(monkeypatch) -> None:
    from chat_downloader.sites.youtube import discovery_playlists
    from chat_downloader.sites.youtube.discovery_playlists import (
        _extract_playlist_items,
    )

    monkeypatch.setattr(
        discovery_playlists,
        "_parse_video",
        lambda video: {"video_id": video["videoId"]},
    )

    videos, token = _extract_playlist_items(
        [
            "malformed",
            None,
            {"unknownRenderer": {}},
            {"playlistVideoRenderer": {"videoId": "after-unknown"}},
        ],
    )

    assert videos == [{"video_id": "after-unknown"}]
    assert token is None


def test_discovery_is_composed_on_youtube_downloader(monkeypatch) -> None:
    from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._get_initial_info",
        lambda *_args, **_kwargs: (
            {
                "contents": {
                    "twoColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "selected": True,
                                    "title": "Videos",
                                    "content": {
                                        "richGridRenderer": {
                                            "contents": [
                                                {
                                                    "richItemRenderer": {
                                                        "content": {
                                                            "videoRenderer": {
                                                                "videoId": "assembled"
                                                            }
                                                        }
                                                    }
                                                }
                                            ]
                                        }
                                    },
                                }
                            }
                        ]
                    }
                }
            },
            {"INNERTUBE_API_KEY": "key"},
            {},
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.discovery._parse_video",
        lambda item: {"video_id": item["videoId"]},
    )
    downloader = object.__new__(YouTubeChatDownloader)

    assert list(downloader.get_user_videos(channel_id="channel-id")) == [
        {"video_id": "assembled"}
    ]


def test_discovery_recurse_past_non_matching_entries() -> None:
    from chat_downloader.sites.youtube.discovery import (
        _iter_playlist_urls,
        _iter_video_ids,
    )

    content = {
        "shelfRenderer": {
            "endpoint": {
                "commandMetadata": {
                    "webCommandMetadata": {"url": "/watch?v=not-playlist"},
                },
            },
        },
        "nested": [
            {"videoRenderer": {"videoId": ""}},
            {
                "shelfRenderer": {
                    "endpoint": {
                        "commandMetadata": {
                            "webCommandMetadata": {"url": "/playlist?list=PL123"},
                        },
                    },
                },
            },
            {"videoRenderer": {"videoId": "abc123"}},
        ],
    }

    assert list(_iter_playlist_urls(content)) == [
        "https://www.youtube.com/playlist?list=PL123",
    ]
    assert list(_iter_video_ids(content)) == ["abc123"]


def test_fetch_browse_continuation_passes_empty_token_without_marking_seen(
    monkeypatch,
) -> None:
    from chat_downloader.sites.youtube import helpers
    from chat_downloader.sites.youtube.helpers import _fetch_browse_continuation

    calls = []

    def fake_get_continuation_info(*_args, **kwargs):
        calls.append((kwargs["json"], kwargs["headers"]))
        return {
            "responseContext": {"visitorData": "visitor-2"},
            "onResponseReceivedActions": [
                {
                    "appendContinuationItemsAction": {
                        "continuationItems": [
                            {"richItemRenderer": {"content": {}}},
                        ],
                    },
                },
            ],
        }

    monkeypatch.setattr(helpers, "_get_continuation_info", fake_get_continuation_info)
    generated_configs = []
    monkeypatch.setattr(
        helpers,
        "_generate_headers",
        lambda ytcfg, *_args: generated_configs.append(ytcfg) or {"X-Test": "api"},
    )
    seen: set[str] = set()
    params = {"context": {"client": {"visitorData": "visitor-1"}}}

    items, yt_info = _fetch_browse_continuation(
        type("Downloader", (), {"_session_post": object()})(),
        "",
        "https://www.youtube.com/youtubei/v1/browse?key=key",
        params,
        {"INNERTUBE_CONTEXT": {"client": {"visitorData": "visitor-1"}}},
        ChatRequest(url="https://www.youtube.com/@example/videos"),
        seen,
    )

    assert items == [{"richItemRenderer": {"content": {}}}]
    assert yt_info is not None
    assert calls == [
        (
            {
                "context": {"client": {"visitorData": "visitor-2"}},
                "continuation": "",
            },
            {"X-Test": "api"},
        )
    ]
    assert generated_configs[0]["INNERTUBE_CONTEXT"]["client"]["visitorData"] == (
        "visitor-1"
    )
    assert params["context"]["client"]["visitorData"] == "visitor-2"
    assert seen == set()

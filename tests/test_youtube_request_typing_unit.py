# SPDX-License-Identifier: MIT

"""Unit tests for typed request flow through YouTube site entry methods."""

from __future__ import annotations

from chat_downloader.models import ChatRequest
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.chat_streams import YouTubeChatStreamsMixin
from chat_downloader.sites.youtube.chat_users_retrieval import (
    YouTubeChatUsersRetrievalMixin,
)
from chat_downloader.sites.youtube.continuation import (
    _get_chat_messages as iterate_chat_messages,
)


def test_youtube_video_entry_accepts_chat_request_and_bridges_later() -> None:
    class DummyYouTubeStreams(YouTubeChatStreamsMixin):
        _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

        def __init__(self) -> None:
            self.initial_request = None
            self.message_params = None

        def _get_initial_video_info(self, video_id, params, video_type="video"):
            self.initial_request = params
            return {"title": "Example"}, {}

        def _get_chat_messages(self, initial_info, ytcfg, params):
            self.message_params = params
            return iter(())

    request = ChatRequest(url="https://www.youtube.com/watch?v=abc", max_messages=3)
    downloader = DummyYouTubeStreams()

    chat = downloader.get_chat_by_video_id("abc", request)

    assert isinstance(chat, Chat)
    assert downloader.initial_request is request
    assert downloader.message_params is request


def test_youtube_video_entry_match_wrapper_calls_video_entry() -> None:
    class DummyYouTubeStreams(YouTubeChatStreamsMixin):
        def get_chat_by_video_id(self, video_id, params):
            return (video_id, params)

    class Match:
        def __init__(self, value: str) -> None:
            self.value = value

        def group(self, _name: str) -> str:
            return self.value

    downloader = DummyYouTubeStreams()
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")
    called_video_id, called_request = downloader._get_chat_by_video_id(
        Match("vid-1"), request
    )

    assert called_video_id == "vid-1"
    assert called_request is request


def test_youtube_clip_entry_match_wrapper_calls_clip_entry() -> None:
    class DummyYouTubeStreams(YouTubeChatStreamsMixin):
        def get_chat_by_clip_id(self, clip_id, params):
            return (clip_id, params)

    class Match:
        def __init__(self, value: str) -> None:
            self.value = value

        def group(self, _name: str) -> str:
            return self.value

    downloader = DummyYouTubeStreams()
    request = ChatRequest(url="https://www.youtube.com/clip/abc")
    called_clip_id, called_request = downloader._get_chat_by_clip_id(
        Match("clip-1"),
        request,
    )

    assert called_clip_id == "clip-1"
    assert called_request is request


def test_youtube_video_initialization_keeps_request_typed_for_video_metadata() -> None:
    class DummyVideoInitialization:
        _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

        def __init__(self) -> None:
            self.parse_request = None

        def _parse_video_data(self, video_id, params, video_type="video"):
            self.parse_request = params
            return (
                {"continuation_info": {"Live chat": "token"}},
                {},
                {"contents": {}},
                {},
            )

    from chat_downloader.sites.youtube.video_initialization import (
        YouTubeVideoInitializationMixin,
    )

    class DummyDownloader(YouTubeVideoInitializationMixin, DummyVideoInitialization):
        pass

    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")
    downloader = DummyDownloader()

    downloader._get_initial_video_info("abc", request)

    assert downloader.parse_request is request


def test_youtube_clip_entry_updates_times_without_mutating_request() -> None:
    class DummyYouTubeStreams(YouTubeChatStreamsMixin):
        _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

        def __init__(self) -> None:
            self.initial_request = None
            self.message_params = None

        def _get_initial_video_info(self, video_id, params, video_type="video"):
            self.initial_request = params
            return {
                "title": "Clip",
                "clip_start_time": 10,
                "clip_end_time": 70,
            }, {}

        def _get_chat_messages(self, initial_info, ytcfg, params):
            self.message_params = params
            return iter(())

    request = ChatRequest(
        url="https://www.youtube.com/clip/abc",
        start_time=5,
        end_time=None,
    )
    downloader = DummyYouTubeStreams()

    downloader.get_chat_by_clip_id("abc", request)

    assert downloader.initial_request is request
    assert request.start_time == 5
    assert request.end_time is None
    assert isinstance(downloader.message_params, ChatRequest)
    assert downloader.message_params.start_time == 15
    assert downloader.message_params.end_time == 70


def test_youtube_user_retrieval_keeps_request_typed_until_discovery_boundary() -> None:
    class DummyYouTubeUsers(YouTubeChatUsersRetrievalMixin):
        _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

        def __init__(self) -> None:
            self.discovery_params = None
            self.video_request = None

        def get_user_videos(self, **kwargs):
            self.discovery_params = kwargs["params"]
            return iter(
                [
                    {
                        "video_id": "abc123",
                        "video_type": "LIVE",
                        "title": "Active stream",
                    },
                ],
            )

        def get_chat_by_video_id(self, video_id, params):
            self.video_request = params
            return Chat(
                iter([{"message_type": "text_message"}]),
                title="Live",
                id=video_id,
            )

    request = ChatRequest(
        url="https://www.youtube.com/@example/live",
        ignore=["skip-me"],
    )
    downloader = DummyYouTubeUsers()

    chat_item = downloader._get_chat_by_user_args({"handle": "example"}, request)
    first_message = next(chat_item.chat)

    assert first_message["message_type"] == "text_message"
    assert downloader.discovery_params is request
    assert downloader.video_request is request


def test_youtube_chat_iteration_passes_typed_request_to_continuation_helper(
    monkeypatch,
) -> None:
    captured = {}

    class DummySession:
        def __init__(self) -> None:
            self.headers = {}

    class DummyDownloader:
        def __init__(self) -> None:
            self.session = DummySession()
            self._session_post = object()

        def check_for_invalid_types(self, *_args, **_kwargs) -> None:
            return None

        def update_session_headers(self, new_headers) -> None:
            self.session.headers.update(new_headers)

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_headers",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._generate_sapisidhash_header",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_innertube_context",
        lambda _ytcfg: {"client": {"visitorData": "visitor"}},
    )

    def fake_get_continuation_info(_url, _session_post, program_params, **_kwargs):
        captured["program_params"] = program_params
        return {
            "continuationContents": {"liveChatContinuation": {"actions": []}},
            "responseContext": {},
        }

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        fake_get_continuation_info,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.parse_continuation_response",
        lambda _yt_info: type(
            "Result",
            (),
            {
                "debug_info": {},
                "timeout_ms": None,
                "is_end": True,
                "next_continuation": None,
            },
        )(),
    )

    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        chat_type="live",
        message_groups=["messages"],
    )
    initial_info = {
        "continuation_info": {
            "Top chat": "top-token",
            "Live chat": "live-token",
        },
        "status": "live",
    }

    list(
        iterate_chat_messages(
            DummyDownloader(),
            initial_info,
            {"INNERTUBE_API_KEY": "key"},
            request,
        ),
    )

    assert captured["program_params"] is request

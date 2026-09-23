# SPDX-License-Identifier: MIT

"""Unit tests for typed request flow through YouTube site entry methods."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.chat_streams import YouTubeChatStreamsMixin
from chat_downloader.sites.youtube.chat_users_retrieval import (
    YouTubeChatUsersRetrievalMixin,
)


class _Streams(YouTubeChatStreamsMixin):
    _coerce_chat_request = staticmethod(BaseChatDownloader._coerce_chat_request)

    def __init__(self, **details):
        self.details = {"title": "Example", **details}
        self.initial_request = self.message_params = None

    def _get_initial_video_info(self, video_id, params, video_type="video"):
        self.initial_request = params
        return self.details, {}

    def _get_chat_messages(self, initial_info, ytcfg, params, diagnostics=None):
        self.message_params = params
        return iter(())


def test_youtube_video_entry_accepts_chat_request_and_bridges_later() -> None:
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc", max_messages=3)
    downloader = _Streams()

    chat = downloader.get_chat_by_video_id("abc", request)

    assert isinstance(chat, Chat)
    assert downloader.initial_request is request
    assert downloader.message_params is request


@pytest.mark.parametrize(
    ("kind", "identifier"), [("video", "vid-1"), ("clip", "clip-1")]
)
def test_youtube_match_wrapper_dispatches_identifier_and_request(
    kind, identifier
) -> None:
    class Streams(YouTubeChatStreamsMixin):
        pass

    setattr(
        Streams, f"get_chat_by_{kind}_id", lambda self, value, params: (value, params)
    )
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")
    result = getattr(Streams(), f"_get_chat_by_{kind}_id")(
        SimpleNamespace(group=lambda _name: identifier),
        request,
    )
    assert result == (identifier, request)
    assert result[1] is request


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
    request = ChatRequest(
        url="https://www.youtube.com/clip/abc",
        start_time=5,
        end_time=None,
    )
    downloader = _Streams(clip_start_time=10, clip_end_time=70)

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

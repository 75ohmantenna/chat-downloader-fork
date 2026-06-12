# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.models import ChatRequest
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.chat_streams import YouTubeChatStreamsMixin
from chat_downloader.sites.youtube.discovery_helpers import (
    YouTubeDiscoveryHelpersMixin,
)
from chat_downloader.sites.youtube.video_status import (
    video_details_to_dict,
)
from chat_downloader.sites.youtube.video_status_models import VideoDetails


def test_video_details_to_dict_serializes_dataclass_fields() -> None:
    details = VideoDetails(
        title="title",
        author="author",
        author_id="author-id",
        original_video_id="orig",
        video_type="video",
        status="live",
        start_time=1.0,
        end_time=2.0,
        duration=3.0,
        continuation_info={"Live chat": "token"},
        clip_start_time=4.0,
        clip_end_time=5.0,
    )

    assert video_details_to_dict(details) == {
        "title": "title",
        "author": "author",
        "author_id": "author-id",
        "original_video_id": "orig",
        "video_type": "video",
        "status": "live",
        "start_time": 1.0,
        "end_time": 2.0,
        "duration": 3.0,
        "continuation_info": {"Live chat": "token"},
        "clip_start_time": 4.0,
        "clip_end_time": 5.0,
    }


def test_channel_discovery_mixin_coerces_typed_request_before_delegating() -> None:
    captured = []

    class DummyDiscovery(YouTubeDiscoveryHelpersMixin):
        def _coerce_chat_request(self, params):
            return {"coerced_from": params.url}

    def fake_get_user_videos(owner, **kwargs):
        captured.append((owner, kwargs))
        yield {"video_id": "one"}

    import chat_downloader.sites.youtube.discovery_helpers as mod

    original = mod.get_user_videos
    mod.get_user_videos = fake_get_user_videos
    try:
        request = ChatRequest(url="https://www.youtube.com/channel/abc/videos")
        result = list(
            DummyDiscovery().get_user_videos(channel_id="abc", params=request)
        )
    finally:
        mod.get_user_videos = original

    assert result == [{"video_id": "one"}]
    assert captured[0][1]["params"] == {
        "coerced_from": "https://www.youtube.com/channel/abc/videos",
    }


def test_chat_streams_mixin_get_chat_messages_delegates_runtime_helper() -> None:
    class DummyStreams(YouTubeChatStreamsMixin):
        pass

    captured = {}

    def fake_get_chat_messages(owner, initial_info, ytcfg, params):
        captured["call"] = (owner, initial_info, ytcfg, params)
        return iter([{"message_type": "text_message"}])

    import chat_downloader.sites.youtube.chat_streams as mod

    original = mod._get_chat_messages
    mod._get_chat_messages = fake_get_chat_messages
    try:
        owner = DummyStreams()
        initial_info = {"a": 1}
        ytcfg = {"b": 2}
        params = ChatRequest(url="https://www.youtube.com/watch?v=abc")
        result = owner._get_chat_messages(initial_info, ytcfg, params)
    finally:
        mod._get_chat_messages = original

    assert list(result) == [{"message_type": "text_message"}]
    assert captured["call"] == (owner, initial_info, ytcfg, params)


def test_chat_streams_mixin_video_entry_wraps_runtime_generator() -> None:
    class DummyStreams(YouTubeChatStreamsMixin):
        def _coerce_chat_request(self, params):
            return params

        def _get_initial_video_info(self, video_id, params, video_type="video"):
            assert video_id == "vid"
            assert video_type == "video"
            return {"title": "Example"}, {"cfg": 1}

        def _get_chat_messages(self, initial_info, ytcfg, params):
            assert initial_info == {"title": "Example"}
            assert ytcfg == {"cfg": 1}
            assert params.url.endswith("vid")
            return iter([{"message_type": "text_message"}])

    request = ChatRequest(url="https://www.youtube.com/watch?v=vid")
    chat = DummyStreams().get_chat_by_video_id("vid", request)

    assert isinstance(chat, Chat)
    assert next(chat.chat) == {"message_type": "text_message"}
    assert chat.id == "vid"


def test_chat_streams_mixin_clip_entry_raises_when_clip_times_missing() -> None:
    class DummyStreams(YouTubeChatStreamsMixin):
        def _coerce_chat_request(self, params):
            return params

        def _get_initial_video_info(self, video_id, params, video_type="video"):
            assert video_type == "clip"
            return {
                "title": "Clip",
                "clip_start_time": None,
                "clip_end_time": 10,
            }, {}

    with pytest.raises(ParsingError, match="Could not determine clip time range"):
        DummyStreams().get_chat_by_clip_id(
            "clip-1",
            ChatRequest(url="https://www.youtube.com/clip/clip-1"),
        )

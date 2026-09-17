# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import Generator

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.models import ChatRequest
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.chat_streams import YouTubeChatStreamsMixin
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


def test_chat_streams_mixin_real_factory_wraps_continuation_loop() -> None:
    class DummyStreams(YouTubeChatStreamsMixin):
        pass

    generator = DummyStreams()._get_chat_messages(
        {},
        {},
        ChatRequest(url="https://www.youtube.com/watch?v=vid"),
    )
    assert isinstance(generator, Generator)
    generator.close()


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

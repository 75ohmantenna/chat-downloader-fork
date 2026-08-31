# SPDX-License-Identifier: MIT

"""Unit tests for typed request flow through Twitch site entry methods."""

from __future__ import annotations

import contextlib
from unittest.mock import Mock, patch

from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader


def test_twitch_vod_entry_accepts_chat_request() -> None:
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://www.twitch.tv/videos/123", max_attempts=2)
    captured = {}

    downloader._download_gql = Mock(
        return_value=[
            {
                "data": {
                    "video": {
                        "title": "Example VOD",
                        "lengthSeconds": 120,
                        "owner": {"login": "example"},
                    },
                },
            },
        ],
    )
    downloader._update_badge_info = Mock()

    def fake_generator(vod_id, params, duration, offset=None):
        captured["vod_id"] = vod_id
        captured["params"] = params
        captured["duration"] = duration
        return iter(())

    downloader._get_chat_messages_by_vod_id = fake_generator

    chat = downloader.get_chat_by_vod_id("123", request)

    assert chat.title == "Example VOD"
    assert captured["vod_id"] == "123"
    assert captured["params"] is request
    assert captured["duration"] == 120


def test_twitch_clip_entry_accepts_chat_request() -> None:
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://clips.twitch.tv/example", max_attempts=2)
    captured = {}

    downloader._download_base_gql = Mock(
        return_value={
            "data": {
                "clip": {
                    "video": {"id": "vod123"},
                    "videoOffsetSeconds": 15,
                    "durationSeconds": 45,
                    "title": "Example Clip",
                    "broadcaster": {"login": "example"},
                },
            },
        },
    )
    downloader._update_badge_info = Mock()

    def fake_generator(vod_id, params, duration, offset=None):
        captured["vod_id"] = vod_id
        captured["params"] = params
        captured["duration"] = duration
        captured["offset"] = offset
        return iter(())

    downloader._get_chat_messages_by_vod_id = fake_generator

    chat = downloader.get_chat_by_clip_id("clip123", request)

    assert chat.title == "Example Clip (clip123)"
    assert captured["vod_id"] == "vod123"
    assert captured["params"] is request
    assert captured["duration"] == 45
    assert captured["offset"] == 15


def test_twitch_stream_entry_accepts_chat_request() -> None:
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://www.twitch.tv/example", max_attempts=2)
    captured = {}

    downloader._download_gql = Mock(
        return_value=[
            {
                "data": {
                    "user": {
                        "stream": {"type": "live"},
                        "lastBroadcast": {"title": "Live Title"},
                    },
                },
            },
        ],
    )
    downloader._update_badge_info = Mock()

    def fake_generator(stream_id, params, *, diagnostics=None):
        captured["stream_id"] = stream_id
        captured["params"] = params
        captured["diagnostics"] = diagnostics
        return iter(())

    downloader._get_chat_messages_by_stream_id = fake_generator

    chat = downloader.get_chat_by_stream_id("example", request)

    assert chat.title == "Live Title"
    assert captured["stream_id"] == "example"
    assert captured["params"] is request
    assert chat.diagnostics is captured["diagnostics"].summary


@patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_stream_id")
@patch("chat_downloader.sites.twitch.extractor.TwitchChatIRC")
def test_twitch_stream_generator_bridges_request_to_legacy_dict(
    mock_irc_class,
    mock_client_generator,
) -> None:
    mock_irc_instance = Mock()
    mock_irc_instance.join_channel = Mock()
    mock_irc_instance.close_connection = Mock()
    mock_irc_class.return_value = mock_irc_instance
    mock_client_generator.return_value = iter([])

    downloader = TwitchChatDownloader()
    request = ChatRequest(
        url="https://www.twitch.tv/example",
        max_attempts=1,
        message_receive_timeout=1.5,
        message_groups=["messages"],
        buffer_size=1234,
    )

    gen = downloader._get_chat_messages_by_stream_id("example", request)

    with contextlib.suppress(StopIteration):
        next(gen)

    call_args = mock_client_generator.call_args
    assert call_args is not None
    assert call_args[0][0] == mock_irc_instance
    assert call_args[0][1] == "example"
    assert call_args[0][2] is request


def test_twitch_client_stream_generator_accepts_chat_request() -> None:
    from chat_downloader.sites.twitch.irc_transport import (
        get_chat_messages_by_stream_id,
    )

    class FakeIRC:
        def __init__(self) -> None:
            self.buffer_sizes = []

        def recv(self, buffer_size) -> str:
            self.buffer_sizes.append(buffer_size)
            return ""

        def send_raw(self, _message) -> None:
            return None

    irc = FakeIRC()
    request = ChatRequest(url="https://www.twitch.tv/example", buffer_size=1234)

    gen = get_chat_messages_by_stream_id(irc, "example", request)

    with contextlib.suppress(ConnectionError):
        next(gen)

    assert irc.buffer_sizes == [1234]

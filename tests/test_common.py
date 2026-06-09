# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat, Image

if TYPE_CHECKING:
    import pathlib

# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


def test_image_basic_initialization() -> None:
    img = Image(url="https://example.com/image.jpg")
    assert img.url == "https://example.com/image.jpg"
    assert img.width is None
    assert img.height is None


def test_image_with_dimensions() -> None:
    img = Image(url="https://example.com/image.jpg", width=100, height=200)
    assert img.url == "https://example.com/image.jpg"
    assert img.width == 100
    assert img.height == 200
    assert img.id == "100x200"


def test_image_with_custom_id() -> None:
    img = Image(
        url="https://example.com/image.jpg",
        width=100,
        height=200,
        image_id="custom_id",
    )
    assert img.id == "custom_id"


def test_image_protocol_relative_url() -> None:
    img = Image(url="//example.com/image.jpg")
    assert img.url == "https://example.com/image.jpg"


def test_image_json_serialization() -> None:
    img = Image(url="https://example.com/image.jpg", width=100, height=200)
    json_data = img.json()
    assert json_data["url"] == "https://example.com/image.jpg"
    assert json_data["width"] == 100
    assert json_data["height"] == 200
    assert json_data["id"] == "100x200"


def test_image_json_excludes_none() -> None:
    img = Image(url="https://example.com/image.jpg")
    json_data = img.json()
    assert json_data["url"] == "https://example.com/image.jpg"
    assert "width" not in json_data
    assert "height" not in json_data
    assert "id" not in json_data


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_chat_basic_initialization() -> None:
    def dummy_generator():
        yield {"message": "test"}

    chat = Chat(chat=dummy_generator())
    assert chat.chat is not None
    assert chat.title is None
    assert chat.duration is None


def test_chat_with_metadata() -> None:
    def dummy_generator():
        yield {"message": "test"}

    chat = Chat(
        chat=dummy_generator(),
        title="Test Stream",
        duration=3600,
        status="past",
        video_type="video",
        start_time=1234567890,
        id="test_id",
    )
    assert chat.title == "Test Stream"
    assert chat.duration == 3600
    assert chat.status == "past"
    assert chat.video_type == "video"
    assert chat.start_time == 1234567890
    assert chat.id == "test_id"


def test_chat_is_iterable() -> None:
    def dummy_generator():
        yield {"message": "first"}
        yield {"message": "second"}

    chat = Chat(chat=dummy_generator())
    assert chat == iter(chat)


def test_chat_iteration() -> None:
    messages = [
        {"message": "first"},
        {"message": "second"},
        {"message": "third"},
    ]

    chat = Chat(chat=iter(messages))
    collected = list(chat)

    assert len(collected) == 3
    assert collected[0]["message"] == "first"
    assert collected[1]["message"] == "second"
    assert collected[2]["message"] == "third"


def test_chat_format_default_returns_str() -> None:
    chat = Chat()
    result = chat.format({"message": "test"})
    assert isinstance(result, str)


def test_chat_stops_iteration() -> None:
    def dummy_generator():
        yield {"message": "first"}
        yield {"message": "second"}

    chat = Chat(chat=dummy_generator())
    first = next(chat)
    second = next(chat)
    assert first["message"] == "first"
    assert second["message"] == "second"
    with pytest.raises(StopIteration):
        next(chat)


def test_chat_writer_initialization() -> None:
    chat = Chat()
    assert chat._output_dispatcher.writers == []
    assert chat._output_dispatcher.callbacks == []


def test_chat_empty_generator() -> None:
    def empty_generator():
        return
        yield

    chat = Chat(chat=empty_generator())
    assert list(chat) == []


def test_chat_closes_writers_when_generator_raises(
    tmp_path: pathlib.Path,
) -> None:
    class FormattedChat(Chat):
        def format(self, item):
            return item["message"]

    def broken_generator():
        yield {"message": "first"}
        msg = "boom"
        raise RuntimeError(msg)

    path = str(tmp_path / "output.jsonl")
    chat = FormattedChat(chat=broken_generator(), title="Test", id="chat-1")
    chat.attach_writer(
        ContinuousWriter(path, overwrite=True, lazy_initialise=True)
    )

    first = next(chat)
    assert first["message"] == "first"

    with pytest.raises(RuntimeError):
        next(chat)

    with open(path, encoding="utf-8") as fh:
        assert json.loads(fh.readline()) == {"message": "first"}

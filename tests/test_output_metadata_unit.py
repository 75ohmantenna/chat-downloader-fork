"""Lazy provider metadata preserves final output selection and alias safety."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.capture_verification import capture_paths
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader


@pytest.mark.parametrize("suffix", ["jsonl", "txt"])
@pytest.mark.parametrize("append", [False, True])
@pytest.mark.parametrize("title", ["Actual Title", "abcdefghijk"])
def test_channel_output_templates_use_discovered_metadata(
    monkeypatch, tmp_path, suffix, append, title
):
    video_id = "abcdefghijk"
    message = {"message_id": "one", "message_type": "text_message", "message": "hello"}
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "get_user_videos",
        lambda *_args, **_kwargs: iter(
            [{"video_id": video_id, "title": title, "video_type": "LIVE"}]
        ),
    )
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_initial_video_info",
        lambda *_args, **_kwargs: (
            {"title": title, "status": "live", "video_type": "video"},
            {},
        ),
    )
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_chat_messages",
        lambda *_args, **_kwargs: iter([message]),
    )
    destinations = {tmp_path / f"{value}.{suffix}" for value in (title, video_id)}
    if append:
        for path in destinations:
            path.write_text("existing\n", encoding="utf-8")
    downloader = ChatDownloader(proxy="")
    try:
        chat = downloader.get_chat(
            "https://www.youtube.com/@audit_channel",
            output=[
                str(tmp_path / ("{id}." + suffix)),
                str(tmp_path / ("{title}." + suffix)),
            ],
            overwrite=not append,
            max_messages=1,
        )
        try:
            assert list(chat) == [message]
            rendered = chat.format(message)
            assert len(chat._output_dispatcher.writers) == len(destinations)
        finally:
            chat.close()
    finally:
        downloader.close()
    assert set(tmp_path.iterdir()) == destinations
    for path in destinations:
        lines = path.read_text(encoding="utf-8").splitlines()
        if append:
            assert lines.pop(0) == "existing"
        assert len(lines) == 1
        assert (json.loads(lines[0]) if suffix == "jsonl" else lines[0]) == (
            message if suffix == "jsonl" else rendered
        )


def test_late_metadata_format_conflicts_preserve_all_files(tmp_path):
    raw, text = tmp_path / "Actual Title.jsonl", tmp_path / "other.txt"
    raw.write_text("preserve\n", encoding="utf-8")
    os.link(raw, text)

    def source():
        chat.title = "Actual Title"
        yield {"message_type": "text_message", "message": "hello"}

    chat = Chat(source())
    configure_chat(
        chat,
        ChatRequest(output=[str(tmp_path / "{title}.jsonl"), str(text)]),
        SimpleNamespace(is_live_status=lambda _: False),
    )
    with pytest.raises(ValueError, match="distinct files"):
        next(chat)
    assert (
        raw.read_text(encoding="utf-8")
        == text.read_text(encoding="utf-8")
        == ("preserve\n")
    )


def test_literal_template_file_aliases_do_not_merge_provisional_outputs(tmp_path):
    literal_id, literal_title = tmp_path / "{id}.jsonl", tmp_path / "{title}.jsonl"
    literal_id.write_text("preserve\n", encoding="utf-8")
    os.link(literal_id, literal_title)
    message = {"message_type": "text_message", "message": "hello"}

    def source():
        chat.id, chat.title, chat._metadata_pending = "video", "Title", False
        yield message

    chat = Chat(source(), title="channel", id="channel")
    chat._metadata_pending = True
    configure_chat(
        chat,
        ChatRequest(output=[str(literal_id), str(literal_title)]),
        SimpleNamespace(is_live_status=lambda _: False),
    )
    assert list(chat) == [message]
    for name in ("video.jsonl", "Title.jsonl"):
        assert json.loads((tmp_path / name).read_text(encoding="utf-8")) == message
    assert (
        literal_id.read_text(encoding="utf-8")
        == literal_title.read_text(encoding="utf-8")
        == "preserve\n"
    )


def test_escaped_fields_are_resolved_before_verification_despite_pending_metadata(
    tmp_path,
):
    chat = Chat(iter(()), title="channel", id="channel")
    chat._metadata_pending = True
    configure_chat(
        chat,
        ChatRequest(
            output=[
                str(tmp_path / "{{id}}.jsonl"),
                f"{tmp_path}/./{{{{id}}}}.jsonl",
                str(tmp_path / "chat.txt"),
            ]
        ),
        SimpleNamespace(is_live_status=lambda _: False),
    )
    assert len(chat._output_dispatcher.writers) == 2
    assert capture_paths(chat) == {
        ".jsonl": tmp_path / "{id}.jsonl",
        ".txt": tmp_path / "chat.txt",
    }
    chat.close()

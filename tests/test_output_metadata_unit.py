"""Lazy provider metadata preserves final output selection and alias safety."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.models import ChatRequest
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.runtime.capture_verification import capture_paths
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader


@pytest.fixture
def youtube_metadata(monkeypatch):
    def configure(title, message):
        monkeypatch.setattr(
            YouTubeChatDownloader,
            "get_user_videos",
            lambda *_args, **_kwargs: iter(
                [{"video_id": "abcdefghijk", "title": title, "video_type": "LIVE"}]
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
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.chat_users_router._get_initial_info",
            lambda *_args: ({}, {}, {"videoDetails": {"videoId": "abcdefghijk"}}),
        )

    return configure


@pytest.mark.parametrize("suffix", ["jsonl", "txt"])
@pytest.mark.parametrize("append", [False, True])
@pytest.mark.parametrize("title", ["Actual Title", "abcdefghijk"])
def test_channel_output_templates_use_discovered_metadata(
    youtube_metadata, tmp_path, suffix, append, title
):
    video_id = "abcdefghijk"
    message = {
        "message_id": "one",
        "message_type": "text_message",
        "message": "hello",
        "author": {"name": "Alice"},
        "timestamp": 1_700_000_000_000_000,
        "time_text": "-0:42",
    }
    youtube_metadata(title, message)
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
            if append:
                with pytest.raises(FileExistsError, match="Generated output filename"):
                    list(chat)
            else:
                assert list(chat) == [message]
                assert chat.diagnostics["termination_reason"] == "message_limit"
                assert len(chat._output_dispatcher.writers) == len(destinations)
            assert isinstance(chat.site, YouTubeChatDownloader)
            assert chat.format(message) == "22:13:20 | Alice: hello"
        finally:
            chat.close()
    finally:
        downloader.close()
    assert set(tmp_path.iterdir()) == destinations
    for path in destinations:
        lines = path.read_text(encoding="utf-8").splitlines()
        if append:
            assert lines == ["existing"]
            continue
        assert len(lines) == 1
        assert (json.loads(lines[0]) if suffix == "jsonl" else lines[0]) == (
            message if suffix == "jsonl" else "22:13:20 | Alice: hello"
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/@audit_channel",
        "https://www.youtube.com/channel/UCR3TOnFWDeAlT-Ho6LueDmg",
        "https://www.youtube.com/@audit_channel/live",
        "https://www.youtube.com/watch?v=abcdefghijk",
    ],
)
@pytest.mark.parametrize("source_timestamp", [False, True])
@pytest.mark.parametrize(
    ("format_name", "source_text"),
    [
        ("default", "22:13:20 | Alice: hello"),
        ("youtube", "22:13:20 | Alice: hello"),
        ("24_hour", "22:13:20 | Alice: hello"),
        ("12_hour", "10:13 PM | Alice: hello"),
        ("youtube_live_default", "22:13:20 | Alice: hello"),
        ("youtube_live_24_hour", "22:13:20 | Alice: hello"),
        ("youtube_live_12_hour", "10:13 PM | Alice: hello"),
        ("custom", "CUSTOM Alice: hello"),
    ],
)
def test_public_youtube_chat_formats_discovered_live_metadata(
    youtube_metadata, tmp_path, url, source_timestamp, format_name, source_text
):
    message = {
        "message_id": "one",
        "message_type": "text_message",
        "message": "hello",
        "author": {"name": "Alice"},
        "received_timestamp": 1_700_000_000_000_000,
    }
    if source_timestamp:
        message.update(timestamp=1_700_000_000_000_000, time_text="-0:42")
    youtube_metadata("Actual Title", message)
    formats = tmp_path / "formats.json"
    formats.write_text(
        json.dumps({"custom": {"template": "CUSTOM {author.name}: {message}"}}),
        encoding="utf-8",
    )
    expected = (
        source_text
        if source_timestamp or format_name == "custom"
        else "2023-11-14 22:13:20 [received] | Alice: hello"
    )
    output = tmp_path / "Actual Title.txt"
    downloader = ChatDownloader(proxy="")
    try:
        chat = downloader.get_chat(
            url,
            format=format_name,
            format_file=str(formats),
            output=[str(tmp_path / "{title}.txt"), str(output)],
            max_messages=1,
        )
        site = chat.site
        try:
            assert isinstance(site, YouTubeChatDownloader)
            assert next(chat) == message
            assert chat.site is site
            assert chat.format(message) == expected
            assert chat.diagnostics["termination_reason"] == "message_limit"
        finally:
            chat.close()
    finally:
        downloader.close()
    assert output.read_text(encoding="utf-8") == expected + "\n"
    assert set(tmp_path.iterdir()) == {formats, output}


def test_channel_discovery_preserves_user_formatter_and_runtime_diagnostics(
    youtube_metadata, monkeypatch, tmp_path
):
    message = {"message_id": "one", "message_type": "text_message", "message": "hello"}
    youtube_metadata("Actual Title", message)

    def source(_site, _info, _config, _request, diagnostics):
        diagnostics["continuation_profile_switch_count"] = 1
        try:
            yield message
        finally:
            diagnostics["continuation_profile_switch_count"] = 2

    monkeypatch.setattr(YouTubeChatDownloader, "_get_chat_messages", source)
    output = tmp_path / "Actual Title.txt"
    downloader = ChatDownloader(proxy="")
    try:
        chat = downloader.get_chat(
            "https://www.youtube.com/@audit_channel",
            output=[str(tmp_path / "{title}.txt"), str(output)],
            max_messages=1,
        )
        diagnostics = chat.diagnostics
        try:
            chat.set_formatter(lambda item: "USER: " + item["message"])
            assert next(chat) == message
            assert chat.format(message) == "USER: hello"
            assert chat.diagnostics is diagnostics
            assert diagnostics == {
                "continuation_profile_switch_count": 1,
                "termination_reason": "message_limit",
            }
        finally:
            chat.close()
        assert diagnostics == {
            "continuation_profile_switch_count": 2,
            "termination_reason": "message_limit",
        }
    finally:
        downloader.close()
    assert output.read_text(encoding="utf-8") == "USER: hello\n"


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


def test_path_observation_does_not_freeze_provisional_metadata(tmp_path):
    message = {"message": "hello"}
    with Chat(iter([message]), title="First", id="first") as chat:
        chat._metadata_pending = True
        configure_chat(
            chat,
            ChatRequest(
                output=[str(tmp_path / "{title}.jsonl"), str(tmp_path / "{id}.txt")],
                format="default",
            ),
            SimpleNamespace(is_live_status=lambda _: False),
        )
        assert capture_paths(chat) == {
            ".jsonl": tmp_path / "First.jsonl",
            ".txt": tmp_path / "first.txt",
        }
        assert not list(tmp_path.iterdir())
        chat.title, chat.id = "Final", "final"
        assert capture_paths(chat) == {
            ".jsonl": tmp_path / "Final.jsonl",
            ".txt": tmp_path / "final.txt",
        }
        assert not list(tmp_path.iterdir())
        assert list(chat) == [message]
    assert {path.name for path in tmp_path.iterdir()} == {"Final.jsonl", "final.txt"}


@pytest.mark.parametrize("configured", [False, True])
def test_output_configuration_retains_previously_attached_writers(tmp_path, configured):
    existing = tmp_path / "existing.jsonl"
    extra = tmp_path / "extra.jsonl"
    with Chat(iter([{"message": "hello"}])) as chat:
        chat.attach_writer(ContinuousWriter(str(existing), lazy_initialise=True))
        configure_chat(
            chat,
            ChatRequest(output=[str(existing), str(extra)] if configured else None),
            SimpleNamespace(is_live_status=lambda _: False),
        )
        assert not list(tmp_path.iterdir())
        assert list(chat) == [{"message": "hello"}]
    assert existing.read_text().count("hello") == 1
    assert extra.exists() is configured

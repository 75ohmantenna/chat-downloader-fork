# SPDX-License-Identifier: MIT

from __future__ import annotations

import inspect
from contextlib import closing
from unittest.mock import Mock

import pytest

from chat_downloader.chat_downloader import ChatDownloader, run
from chat_downloader.models import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ChatRequest,
)
from chat_downloader.sites import TwitchChatDownloader, YouTubeChatDownloader
from chat_downloader.sites.models import Chat


def test_chat_downloader_init_defaults_are_canonical_model_defaults() -> None:
    signature = inspect.signature(ChatDownloader)

    assert signature.parameters["connect_timeout"].default == DEFAULT_CONNECT_TIMEOUT
    assert signature.parameters["read_timeout"].default == DEFAULT_READ_TIMEOUT


def test_run_delegates_to_runtime_execute_run(monkeypatch) -> None:
    execute_calls = []

    def fake_execute_run(
        downloader_cls,
        propagate_interrupt=False,
        **kwargs,
    ) -> None:
        execute_calls.append((downloader_cls, propagate_interrupt, kwargs))

    monkeypatch.setattr(
        "chat_downloader.chat_downloader.execute_run",
        fake_execute_run,
    )

    run(
        propagate_interrupt=True,
        url="https://www.youtube.com/watch?v=abc",
        max_messages=1,
    )

    assert execute_calls == [
        (
            ChatDownloader,
            True,
            {
                "url": "https://www.youtube.com/watch?v=abc",
                "max_messages": 1,
            },
        ),
    ]


def test_chat_downloader_init_logs_sanitized_proxy_and_headers(
    monkeypatch,
) -> None:
    messages: list[str] = []

    def fake_log(level: str, message: str) -> None:
        if level == "debug":
            messages.append(message)

    monkeypatch.setattr("chat_downloader.chat_downloader.log", fake_log)

    ChatDownloader(
        headers={"Authorization": "Bearer secret", "User-Agent": "Agent/1.0"},
        proxy="http://user:pass@example.invalid:8080",
    )

    init_log = next(
        msg for msg in messages if msg.startswith("Initialization parameters:")
    )
    assert "Bearer secret" not in init_log
    assert "user:pass@example.invalid" not in init_log
    assert "<redacted>" in init_log


def test_chat_downloader_init_logs_sanitized_cookies(monkeypatch) -> None:
    messages: list[str] = []

    def fake_log(level: str, message: str) -> None:
        if level == "debug":
            messages.append(message)

    monkeypatch.setattr("chat_downloader.chat_downloader.log", fake_log)

    ChatDownloader(cookies="/tmp/cookies.txt", proxy="")

    init_log = next(
        msg for msg in messages if msg.startswith("Initialization parameters:")
    )
    assert "/tmp/cookies.txt" not in init_log
    assert "<redacted>" in init_log


@pytest.mark.parametrize(
    ("url_args", "expected_url"),
    [
        ((), ""),
        ((None,), ""),
        (("https://example.invalid/watch?v=1",), "https://example.invalid/watch?v=1"),
    ],
    ids=["omitted", "explicit-none", "supplied"],
)
def test_get_chat_builds_chat_request_and_delegates(
    monkeypatch, url_args, expected_url
) -> None:
    downloader = ChatDownloader()
    seen: list[ChatRequest] = []

    def fake_get_chat_request(request: ChatRequest):
        seen.append(request)
        return "chat"

    monkeypatch.setattr(downloader, "get_chat_request", fake_get_chat_request)

    result = downloader.get_chat(
        *url_args,
        output="out.jsonl",
        message_types=["text_message"],
        ignore=["abc"],
        buffer_size=123,
    )

    assert result == "chat"
    assert len(seen) == 1
    assert seen[0].url == expected_url
    assert seen[0].output == "out.jsonl"
    assert seen[0].message_types == ["text_message"]
    assert seen[0].ignore == ["abc"]
    assert seen[0].buffer_size == 123


def test_get_chat_request_delegates_to_deep_dispatch_interface(monkeypatch) -> None:
    downloader = ChatDownloader()
    request = ChatRequest(url="https://example.invalid/watch?v=1")
    chat = object()
    calls: list[tuple[ChatDownloader, ChatRequest]] = []
    monkeypatch.setattr(
        "chat_downloader.chat_downloader.dispatch_chat",
        lambda owner, incoming: calls.append((owner, incoming)) or chat,
    )

    assert downloader.get_chat_request(request) is chat
    assert calls == [(downloader, request)]


@pytest.mark.parametrize("prefix", ["", "//", "https://"])
@pytest.mark.parametrize(
    ("host_path", "provider", "method", "resource_id"),
    [
        (
            "www.youtube.com/watch?v=wXspodtIxYU",
            YouTubeChatDownloader,
            "get_chat_by_video_id",
            "wXspodtIxYU",
        ),
        ("twitch.tv/xenova", TwitchChatDownloader, "get_chat_by_stream_id", "xenova"),
        (
            "www.twitch.tv/xenova",
            TwitchChatDownloader,
            "get_chat_by_stream_id",
            "xenova",
        ),
    ],
    ids=["youtube-watch", "twitch-bare-host", "twitch-www-host"],
)
def test_get_chat_normalizes_and_dispatches_to_provider(
    monkeypatch, prefix, host_path, provider, method, resource_id
) -> None:
    handler = Mock(return_value=Chat(iter(()), id=resource_id))
    monkeypatch.setattr(provider, method, handler)

    with (
        closing(ChatDownloader()) as downloader,
        downloader.get_chat(prefix + host_path, max_messages=10) as chat,
    ):
        assert type(chat.site) is provider
        assert chat.id == resource_id
        handler.assert_called_once()
        actual_id, request = handler.call_args.args
        assert actual_id == resource_id
        assert isinstance(request, ChatRequest)
        assert request.url == "https://" + host_path
        assert request.max_messages == 10

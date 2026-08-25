# SPDX-License-Identifier: MIT

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from chat_downloader.chat_downloader import ChatDownloader, run
from chat_downloader.models import (
    CHAT_PARAM_NAMES,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ChatRequest,
)


def test_chat_downloader_session_helpers_delegate_to_runtime_helpers(
    monkeypatch,
) -> None:
    downloader = ChatDownloader()
    pool = downloader._session_pool
    pool.create = MagicMock(return_value="created")
    pool.get = MagicMock(return_value="existing-session")
    pool.close = MagicMock()

    class FakeSite:
        __name__ = "FakeSite"

    assert downloader.create_session(FakeSite, overwrite=True) == "created"
    assert downloader.get_session(FakeSite) == "existing-session"
    downloader.close()
    pool.create.assert_called_once_with(FakeSite, overwrite=True)
    pool.get.assert_called_once_with(FakeSite)
    pool.close.assert_called_once_with()


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

    ChatDownloader(cookies="/tmp/cookies.txt")

    init_log = next(
        msg for msg in messages if msg.startswith("Initialization parameters:")
    )
    assert "/tmp/cookies.txt" not in init_log
    assert "<redacted>" in init_log


def test_get_chat_signature_matches_chat_request_fields() -> None:
    sig = inspect.signature(ChatDownloader.get_chat)
    params = set(sig.parameters) - {"self"}
    assert params == set(CHAT_PARAM_NAMES)


def test_get_chat_builds_chat_request_and_delegates(monkeypatch) -> None:
    downloader = ChatDownloader()
    seen: list[ChatRequest] = []

    def fake_get_chat_request(request: ChatRequest):
        seen.append(request)
        return "chat"

    monkeypatch.setattr(downloader, "get_chat_request", fake_get_chat_request)

    result = downloader.get_chat(
        url=None,
        output="out.jsonl",
        message_types=["text_message"],
        ignore=["abc"],
        buffer_size=123,
    )

    assert result == "chat"
    assert len(seen) == 1
    assert seen[0].url == ""
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

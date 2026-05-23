# SPDX-License-Identifier: MIT

import inspect
from typing import Any, cast

from chat_downloader.chat_downloader import ChatDownloader, run
from chat_downloader.models import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ChatRequest,
)


def test_chat_downloader_session_helpers_delegate_to_runtime_helpers(
    monkeypatch,
) -> None:
    downloader = ChatDownloader()
    calls: list[tuple[Any, ...]] = []

    def fake_create_runtime_session(owner, cls, overwrite=False) -> str:
        calls.append(("create", owner, cls, overwrite))
        return "created"

    def fake_close_sessions(owner) -> None:
        calls.append(("close", owner))

    monkeypatch.setattr(
        "chat_downloader.chat_downloader.create_runtime_session",
        fake_create_runtime_session,
    )
    monkeypatch.setattr(
        "chat_downloader.chat_downloader.close_sessions",
        fake_close_sessions,
    )

    class FakeSite:
        __name__ = "FakeSite"

    downloader.sessions["FakeSite"] = cast("Any", "existing-session")

    assert downloader.create_session(FakeSite, overwrite=True) == "created"
    assert downloader.get_session(FakeSite) == "existing-session"

    downloader.close()

    assert calls[0] == ("create", downloader, FakeSite, True)
    assert calls[1] == ("close", downloader)


def test_chat_downloader_init_defaults_are_canonical_model_defaults() -> None:
    signature = inspect.signature(ChatDownloader)

    assert (
        signature.parameters["connect_timeout"].default
        == DEFAULT_CONNECT_TIMEOUT
    )
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
        msg for msg in messages if msg.startswith("Initialisation parameters:")
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
        msg for msg in messages if msg.startswith("Initialisation parameters:")
    )
    assert "/tmp/cookies.txt" not in init_log
    assert "<redacted>" in init_log


def test_run_time_program_parameters_log_is_sanitized(monkeypatch) -> None:
    from chat_downloader.runtime.site_dispatch import create_chat_for_site
    from chat_downloader.sites.models import Chat

    logged_messages: list[str] = []

    class FakeSite:
        _NAME = "example.invalid"

        def get_site_value(self, value):
            return value

        def get_chat_by_id(self, _match, _request):
            return Chat(
                iter(()), title="Example title", status="live", id="abc"
            )

    class FakeOwner:
        def __init__(self) -> None:
            self.sessions = {}

        def create_session(self, site):
            session = site()
            self.sessions[site.__name__] = session
            return session

    def fake_log(level: str, message: str) -> None:
        if level == "debug":
            logged_messages.append(message)

    monkeypatch.setattr("chat_downloader.runtime.site_dispatch.log", fake_log)
    monkeypatch.setattr(
        ChatRequest,
        "as_dict",
        lambda self: {
            "url": self.url,
            "headers": {"Authorization": "Bearer secret"},
            "proxy": "http://user:pass@example.invalid:8080",
        },
    )

    create_chat_for_site(
        FakeOwner(),
        FakeSite,
        ("get_chat_by_id", None),
        ChatRequest(url="https://example.invalid/watch?id=abc"),
    )

    program_log = next(
        msg for msg in logged_messages if msg.startswith("Program parameters:")
    )
    assert "Bearer secret" not in program_log
    assert "user:pass@example.invalid" not in program_log
    assert "<redacted>" in program_log


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


def test_get_chat_request_returns_site_chat_when_supported(monkeypatch) -> None:
    downloader = ChatDownloader()
    request = ChatRequest(url="https://example.invalid/watch?v=1")
    chat = object()

    monkeypatch.setattr(
        "chat_downloader.chat_downloader.validate_url", lambda _url: None
    )
    monkeypatch.setattr(
        "chat_downloader.chat_downloader.try_create_chat_from_sites",
        lambda _owner, _url, _request: chat,
    )

    assert downloader.get_chat_request(request) is chat


def test_get_chat_request_falls_back_to_unsupported_handler(
    monkeypatch,
) -> None:
    downloader = ChatDownloader()
    request = ChatRequest(url="https://example.invalid/watch?v=1")
    handled = object()

    monkeypatch.setattr(
        "chat_downloader.chat_downloader.validate_url", lambda _url: None
    )
    monkeypatch.setattr(
        "chat_downloader.chat_downloader.try_create_chat_from_sites",
        lambda _owner, _url, _request: None,
    )
    monkeypatch.setattr(
        "chat_downloader.chat_downloader.handle_unsupported_url",
        lambda _owner, _url, _request: handled,
    )

    assert downloader.get_chat_request(request) is handled

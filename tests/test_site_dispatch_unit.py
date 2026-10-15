# SPDX-License-Identifier: MIT

from __future__ import annotations

from threading import Event
from typing import Any, ClassVar
from unittest.mock import Mock

import pytest

from chat_downloader.errors import (
    ChatGeneratorError,
    FormatFileNotFound,
    InvalidURL,
    SiteNotSupported,
    URLNotProvided,
)
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.site_dispatch import _chat_debug_snapshot, dispatch_chat
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat
from chat_downloader.utils.timed_generator import polling_sleep


class _Owner:
    def __init__(self) -> None:
        self.sessions: dict[str, BaseChatDownloader] = {}

    def create_session(
        self, site: type[BaseChatDownloader], *, overwrite: bool = False
    ) -> BaseChatDownloader:
        assert overwrite is False
        session = site()
        self.sessions[site.__name__] = session
        return session


class _GoodSite(BaseChatDownloader):
    _NAME = "example.invalid"
    _VALID_URLS: ClassVar[dict[str, str]] = {
        "get_chat_by_id": r"https://example\.invalid/watch/(?P<id>[^/?#]+)"
    }
    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {
        "message_groups": ["resolved"],
        "format": "default",
    }
    seen_request: ClassVar[ChatRequest | None] = None

    def get_chat_by_id(self, match, request: ChatRequest) -> Chat:
        type(self).seen_request = request
        return Chat(
            iter(({"message_type": "text_message"},)),
            title=f"Example {match.group('id')}",
            status="live",
            id=match.group("id"),
        )


def _install_sites(monkeypatch, *sites: type[BaseChatDownloader]) -> None:
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.get_all_sites", lambda: list(sites)
    )


def test_dispatch_chat_rejects_missing_malformed_and_unknown_urls(monkeypatch) -> None:
    _install_sites(monkeypatch, _GoodSite)
    owner = _Owner()

    with pytest.raises(URLNotProvided):
        dispatch_chat(owner, ChatRequest())
    with pytest.raises(InvalidURL):
        dispatch_chat(owner, ChatRequest(url="https://"))
    with pytest.raises(SiteNotSupported, match=r"www\.example\.com"):
        dispatch_chat(owner, ChatRequest(url="https://www.example.com/watch"))


@pytest.mark.parametrize(
    "url",
    [
        "example.invalid/watch/abc",
        "//example.invalid/watch/abc",
    ],
)
def test_dispatch_chat_normalizes_scheme_and_configures_typed_chat(
    monkeypatch, url: str
) -> None:
    _install_sites(monkeypatch, _GoodSite)
    request = ChatRequest(
        url=url,
        max_messages=1,
    )

    chat = dispatch_chat(_Owner(), request)

    assert chat.title == "Example abc"
    assert chat.site is not None
    assert list(chat) == [{"message_type": "text_message"}]
    assert _GoodSite.seen_request is not None
    assert _GoodSite.seen_request.url == "https://example.invalid/watch/abc"
    assert _GoodSite.seen_request.message_groups == ["resolved"]


def test_dispatch_chat_uses_first_matching_site(monkeypatch) -> None:
    class NoMatchSite(_GoodSite):
        _VALID_URLS: ClassVar[dict[str, str]] = {}

    _install_sites(monkeypatch, NoMatchSite, _GoodSite)

    chat = dispatch_chat(
        _Owner(), ChatRequest(url="https://example.invalid/watch/first")
    )

    assert chat.id == "first"


@pytest.mark.parametrize("returns_none", [False, True])
def test_dispatch_chat_rejects_missing_or_empty_site_handler(
    monkeypatch, returns_none: bool
) -> None:
    class BrokenSite(BaseChatDownloader):
        _NAME = "broken.invalid"
        _VALID_URLS: ClassVar[dict[str, str]] = {
            "get_chat_by_id": r"https://broken\.invalid/(?P<id>[^/?#]+)"
        }

        if returns_none:

            def get_chat_by_id(self, _match, _request) -> None:
                return None

    _install_sites(monkeypatch, BrokenSite)
    error = ChatGeneratorError if returns_none else NotImplementedError

    with pytest.raises(error):
        dispatch_chat(_Owner(), ChatRequest(url="https://broken.invalid/abc"))


def test_dispatch_chat_logs_only_sanitized_chat_snapshot(monkeypatch, tmp_path) -> None:
    logs: list[str] = []
    _install_sites(monkeypatch, _GoodSite)
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.log",
        lambda level, message: logs.append(str(message)) if level == "debug" else None,
    )

    chat = dispatch_chat(
        _Owner(),
        ChatRequest(
            url="https://example.invalid/watch/abc",
            output=str(tmp_path / "out.jsonl"),
        ),
    )

    snapshot = _chat_debug_snapshot(chat)
    assert snapshot["writer_count"] == 1
    assert snapshot["callback_count"] == 0
    chat_log = next(
        message for message in logs if message.startswith("Chat information:")
    )
    assert "Example abc" in chat_log
    assert "session" not in chat_log.lower()


def test_chat_debug_snapshot_omits_counts_without_dispatcher() -> None:
    snapshot = _chat_debug_snapshot(Chat(iter(()), title="t", id="x"))
    assert "writer_count" not in snapshot
    assert "callback_count" not in snapshot


def test_dispatch_chat_preserves_site_on_configured_chat(monkeypatch) -> None:
    _install_sites(monkeypatch, _GoodSite)
    owner = _Owner()
    chat = dispatch_chat(owner, ChatRequest(url="https://example.invalid/watch/abc"))
    assert chat.site is owner.sessions["_GoodSite"]
    assert isinstance(chat.site, BaseChatDownloader)


@pytest.mark.parametrize("failure", ["format", "output"])
def test_failed_configuration_closes_source_without_starting_worker(
    monkeypatch, tmp_path, failure
):
    source = Mock()
    chat = Chat(source)
    _install_sites(monkeypatch, _GoodSite)
    monkeypatch.setattr(_GoodSite, "get_chat_by_id", lambda *_: chat)
    options = (
        {"format_file": str(tmp_path / "missing.json")}
        if failure == "format"
        else {"output": [str(tmp_path / "ok.jsonl"), str(tmp_path / "bad.csv")]}
    )
    with pytest.raises(FormatFileNotFound if failure == "format" else ValueError):
        dispatch_chat(
            _Owner(),
            ChatRequest(
                url="https://example.invalid/watch/test", timeout=60, **options
            ),
        )
    source.close.assert_called_once()
    assert chat.chat is source
    assert not list(tmp_path.iterdir())
    if chat._output_dispatcher is not None:
        assert chat._output_dispatcher.closed


def test_configuration_failure_preserves_error_when_cleanup_fails(monkeypatch):
    chat = Chat(iter(()))
    chat.close = Mock(side_effect=RuntimeError("cleanup failed"))
    _install_sites(monkeypatch, _GoodSite)
    monkeypatch.setattr(_GoodSite, "get_chat_by_id", lambda *_: chat)
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.configure_chat",
        Mock(side_effect=KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        dispatch_chat(_Owner(), ChatRequest(url="https://example.invalid/watch/test"))
    chat.close.assert_called_once()


def test_failure_after_worker_start_closes_the_running_source(monkeypatch):
    entered, closed = Event(), Event()

    def messages():
        try:
            entered.set()
            polling_sleep(60)
            yield {}
        finally:
            closed.set()

    chat = Chat(messages())
    _install_sites(monkeypatch, _GoodSite)
    monkeypatch.setattr(_GoodSite, "get_chat_by_id", lambda *_: chat)

    def log(_level, message):
        if str(message).startswith("Chat information:"):
            assert entered.wait(2)
            raise RuntimeError("failed after configuration")

    monkeypatch.setattr("chat_downloader.runtime.site_dispatch.log", log)
    try:
        with pytest.raises(RuntimeError, match="failed after configuration"):
            dispatch_chat(
                _Owner(),
                ChatRequest(url="https://example.invalid/watch/test", timeout=60),
            )
        assert closed.wait(2)
        assert not chat.chat._worker.is_alive()
    finally:
        chat.close()

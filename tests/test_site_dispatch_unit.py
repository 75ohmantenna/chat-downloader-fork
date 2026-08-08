# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from chat_downloader.errors import (
    ChatGeneratorError,
    InvalidURL,
    SiteNotSupported,
    URLNotProvided,
)
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.site_dispatch import _chat_debug_snapshot, dispatch_chat
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat


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

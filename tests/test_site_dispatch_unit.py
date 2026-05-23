# SPDX-License-Identifier: MIT

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_downloader.errors import InvalidURL, SiteNotSupported, URLNotProvided
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.site_dispatch import (
    create_chat_for_site,
    execute_chat_generator,
    handle_unsupported_url,
    resolve_site_defaults,
    try_create_chat_from_sites,
    validate_url,
)
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat, SiteDefault


def test_validate_url_rejects_missing_values() -> None:
    with pytest.raises(URLNotProvided):
        validate_url("")

    with pytest.raises(URLNotProvided):
        validate_url(None)


def test_handle_unsupported_url_retries_with_https() -> None:
    owner = MagicMock()
    owner.get_chat_request.return_value = "chat-object"

    request = ChatRequest(url="www.youtube.com/watch?v=abc", max_messages=25)
    result = handle_unsupported_url(owner, request.url, request)

    assert result == "chat-object"
    owner.get_chat_request.assert_called_once()
    upgraded_request = owner.get_chat_request.call_args.args[0]
    assert isinstance(upgraded_request, ChatRequest)
    assert upgraded_request.url == "https://www.youtube.com/watch?v=abc"
    assert upgraded_request.max_messages == 25


def test_handle_unsupported_url_rejects_unknown_site() -> None:
    owner = MagicMock()

    with pytest.raises(SiteNotSupported):
        handle_unsupported_url(
            owner,
            "https://www.example.com/watch",
            ChatRequest(url="x"),
        )


def test_handle_unsupported_url_rejects_malformed_url() -> None:
    owner = MagicMock()

    with pytest.raises(InvalidURL):
        handle_unsupported_url(owner, "https://", ChatRequest(url="x"))


def test_execute_chat_generator_rejects_missing_method() -> None:
    with pytest.raises(NotImplementedError):
        execute_chat_generator(
            object(),
            "get_chat_by_id",
            match=None,
            request=ChatRequest(url="https://example.invalid"),
            site_name="ExampleSite",
        )


def test_execute_chat_generator_rejects_none_result() -> None:
    class FakeSite(BaseChatDownloader):
        def get_chat_by_id(self, _match, _params) -> None:
            return None

    site = FakeSite()

    with pytest.raises(Exception) as excinfo:
        execute_chat_generator(
            site,
            "get_chat_by_id",
            match=None,
            request=ChatRequest(url="https://example.invalid"),
            site_name="ExampleSite",
        )

    assert "No valid generator found" in str(excinfo.value)


def test_execute_chat_generator_passes_typed_request_directly() -> None:
    class FakeSite(BaseChatDownloader):
        def get_chat_by_id(self, _match, params):
            return params

    request = ChatRequest(
        url="https://example.invalid/watch?id=1", max_messages=7
    )
    site = FakeSite()

    result = execute_chat_generator(
        site,
        "get_chat_by_id",
        match=None,
        request=request,
        site_name="FakeSite",
    )

    assert isinstance(result, ChatRequest)
    assert result.url == "https://example.invalid/watch?id=1"
    assert result.max_messages == 7


def test_resolve_site_defaults_returns_typed_request() -> None:
    request = ChatRequest(url="https://example.invalid")
    site = SimpleNamespace(
        get_site_value=lambda value: (
            "resolved" if isinstance(value, SiteDefault) else value
        ),
    )

    resolved = resolve_site_defaults(request, site)

    assert isinstance(resolved, ChatRequest)
    assert resolved is not request
    assert resolved.url == "https://example.invalid"
    assert resolved.message_groups == "resolved"
    assert resolved.format == "resolved"


def test_create_chat_for_site_configures_chat_without_owner_wrapper() -> None:
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

    owner = FakeOwner()

    chat = create_chat_for_site(
        owner,
        FakeSite,
        ("get_chat_by_id", None),
        ChatRequest(url="https://example.invalid/watch?id=abc"),
    )

    assert chat.title == "Example title"
    assert chat.site is owner.sessions["FakeSite"]


def test_create_chat_for_site_logs_sanitized_chat_snapshot(monkeypatch) -> None:
    logged_messages: list[str] = []

    def fake_log(level: str, message: str) -> None:
        if level == "debug":
            logged_messages.append(message)

    monkeypatch.setattr("chat_downloader.runtime.site_dispatch.log", fake_log)

    class FakeSession:
        headers = {"Authorization": "secret"}
        cookies = {"SID": "cookie"}

    class FakeSite:
        _NAME = "example.invalid"

        def __init__(self) -> None:
            self.session = FakeSession()

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

    owner = FakeOwner()

    create_chat_for_site(
        owner,
        FakeSite,
        ("get_chat_by_id", None),
        ChatRequest(url="https://example.invalid/watch?id=abc"),
    )

    chat_logs = [
        message
        for message in logged_messages
        if message.startswith("Chat information:")
    ]
    assert chat_logs
    combined = "\n".join(chat_logs)
    assert "Example title" in combined
    assert "Authorization" not in combined
    assert "secret" not in combined
    assert "cookie" not in combined


def test_try_create_chat_from_sites_returns_first_matching_site(
    monkeypatch,
) -> None:
    owner = object()
    request = ChatRequest(url="https://example.invalid/watch?id=abc")

    class NoMatchSite:
        @staticmethod
        def matches(_url) -> None:
            return None

    class MatchSite:
        @staticmethod
        def matches(_url):
            return ("get_chat_by_id", "match")

    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.get_all_sites",
        lambda: [NoMatchSite, MatchSite],
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.create_chat_for_site",
        lambda current_owner, site, match_info, current_request: (
            current_owner,
            site,
            match_info,
            current_request,
        ),
    )

    result = try_create_chat_from_sites(owner, request.url, request)

    assert result == (owner, MatchSite, ("get_chat_by_id", "match"), request)


def test_try_create_chat_from_sites_returns_none_without_match(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.runtime.site_dispatch.get_all_sites",
        list,
    )

    assert (
        try_create_chat_from_sites(
            object(),
            "https://example.invalid/watch?id=abc",
            ChatRequest(url="https://example.invalid/watch?id=abc"),
        )
        is None
    )

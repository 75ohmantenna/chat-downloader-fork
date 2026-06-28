# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chat_downloader.errors import RetriesExceeded
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import live_service
from chat_downloader.sites.kick.constants import CHAT_MESSAGE_EVENT
from chat_downloader.sites.kick.errors import KickError
from tests.kick_helpers import (
    FakeDownloader,
    FakeKickSession,
    FakeResponse,
    FakeTransport,
    load_fixture,
    make_frame_iterator,
    pusher_frame,
)


def _request(**overrides: Any) -> ChatRequest:
    params: dict[str, Any] = {
        "url": "https://kick.com/examplechannel",
        "max_attempts": 2,
        "retry_timeout": 0,
        "interruptible_retry": False,
    }
    params.update(overrides)
    return ChatRequest.from_kwargs(**params)


class _NoRetryDownloader:
    """Downloader whose ``retry`` never sleeps or raises (drives exhaustion)."""

    @staticmethod
    def retry(*_args: Any, **_kwargs: Any) -> None:
        return None

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self._http_timeout = (10.0, 30.0)

    def _session_get(self, _url: str, **_kwargs: Any) -> Any:
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


# ── _resolve_proxy ────────────────────────────────────────────────────────────


class _DownloaderWithProxy:
    """Minimal downloader stub with a session that has proxy configured."""

    def __init__(self) -> None:
        self.session = MagicMock()
        self.session.proxies = {"https": "http://proxy.example:8080"}


class _DownloaderWithEmptyProxy:
    """Downloader with a session but no proxy configured."""

    def __init__(self) -> None:
        self.session = MagicMock()
        self.session.proxies = {}


def test_resolve_proxy_returns_proxy_dict() -> None:
    proxy = live_service._resolve_proxy(_DownloaderWithProxy())
    assert proxy == {"https": "http://proxy.example:8080"}


def test_resolve_proxy_returns_none_for_empty_proxies() -> None:
    proxy = live_service._resolve_proxy(_DownloaderWithEmptyProxy())
    assert proxy is None


def test_resolve_proxy_returns_none_without_session() -> None:
    proxy = live_service._resolve_proxy(object())
    assert proxy is None


# ── _resolve_ws_proxy ─────────────────────────────────────────────────────────


def test_resolve_ws_proxy_returns_host_port() -> None:
    downloader = _DownloaderWithProxy()
    host, port = live_service._resolve_ws_proxy(downloader)
    assert host == "proxy.example"
    assert port == 8080


def test_resolve_ws_proxy_returns_none_for_empty_proxies() -> None:
    host, port = live_service._resolve_ws_proxy(_DownloaderWithEmptyProxy())
    assert host is None
    assert port is None


def test_resolve_ws_proxy_returns_none_without_session() -> None:
    host, port = live_service._resolve_ws_proxy(object())
    assert host is None
    assert port is None


def test_resolve_ws_proxy_returns_none_for_empty_url() -> None:
    """Proxies with empty string value yields None."""
    downloader = _DownloaderWithEmptyProxy()
    downloader.session.proxies = {"https": ""}
    host, port = live_service._resolve_ws_proxy(downloader)
    assert host is None
    assert port is None


# ── _resolve_channel ──────────────────────────────────────────────────────────


def test_resolve_channel_live() -> None:
    data = load_fixture("channel_live.json")
    channel_id, chatroom_id, title = live_service._resolve_channel(
        data, "examplechannel"
    )
    assert channel_id == "12345"
    assert chatroom_id == "54321"
    assert title == "Example live stream title"


def test_resolve_channel_missing_channel_id() -> None:
    with pytest.raises(KickError, match="channel id"):
        live_service._resolve_channel({"chatroom": {"id": 1}}, "x")


def test_resolve_channel_missing_chatroom_id() -> None:
    data = load_fixture("channel_missing_chatroom.json")
    with pytest.raises(KickError, match="chatroom id"):
        live_service._resolve_channel(data, "examplechannel")


def test_resolve_channel_rejects_non_numeric_ids() -> None:
    with pytest.raises(KickError, match="non-numeric"):
        live_service._resolve_channel(
            {"id": "../channel", "chatroom": {"id": "room"}},
            "examplechannel",
        )


def test_resolve_channel_offline() -> None:
    data = load_fixture("channel_offline.json")
    channel_id, chatroom_id, title = live_service._resolve_channel(
        data, "examplechannel"
    )
    assert channel_id == "12345"
    assert chatroom_id == "54321"
    assert title == "examplechannel"


def test_resolve_channel_live_without_title_falls_back_to_username() -> None:
    data = {"id": 1, "chatroom": {"id": 2}, "livestream": {}}
    _cid, _rid, title = live_service._resolve_channel(data, "fallbackname")
    assert title == "fallbackname"


# ── _fetch_channel_with_retry ─────────────────────────────────────────────────


def test_fetch_channel_with_retry_succeeds_after_transient() -> None:
    payload = load_fixture("channel_live.json")
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(503, {"e": 1}), FakeResponse(200, payload)])
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        data = live_service._fetch_channel_with_retry(
            downloader, "examplechannel", _request()
        )
    assert data["id"] == 12345


def test_fetch_channel_with_retry_exhausts() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(500, {"e": 1})])
    with (
        patch(
            "chat_downloader.sites.kick.api_client._get_kick_session",
            return_value=session,
        ),
        pytest.raises(RetriesExceeded),
    ):
        live_service._fetch_channel_with_retry(
            downloader, "x", _request(max_attempts=1)
        )


def test_fetch_channel_with_retry_unreachable_guard() -> None:
    # retry that never raises lets the loop exhaust, hitting the guard.
    downloader = _NoRetryDownloader()
    session = FakeKickSession([FakeResponse(500, {"e": 1})])
    with (
        patch(
            "chat_downloader.sites.kick.api_client._get_kick_session",
            return_value=session,
        ),
        pytest.raises(RuntimeError, match="unreachable"),
    ):
        live_service._fetch_channel_with_retry(
            downloader, "x", _request(max_attempts=1)
        )


# ── _open_subscribed_transport ────────────────────────────────────────────────


def test_open_subscribed_transport_retries_then_succeeds() -> None:
    transport = FakeTransport(connect_errors=1)
    opened = live_service._open_subscribed_transport(
        FakeDownloader(), "54321", _request(), lambda: transport
    )
    assert opened is transport
    assert transport.connected is True
    assert transport.subscribed_to == "54321"
    assert transport.close_count == 1  # the failed attempt closed its transport


def test_open_subscribed_transport_separates_connect_and_receive_timeouts() -> None:
    transport = FakeTransport()
    downloader = FakeDownloader(connect_timeout=7.5, read_timeout=22.0)
    opened = live_service._open_subscribed_transport(
        downloader,
        "54321",
        _request(message_receive_timeout=0.1),
        lambda: transport,
    )

    assert opened is transport
    assert transport.connect_timeout == pytest.approx(7.5)
    assert transport.receive_timeout == pytest.approx(1.0)
    assert transport.subscribed_to == "54321"


def test_open_subscribed_transport_unreachable_guard() -> None:
    with pytest.raises(RuntimeError, match="unreachable"):
        live_service._open_subscribed_transport(
            _NoRetryDownloader(),
            "1",
            _request(max_attempts=1),
            lambda: FakeTransport(connect_errors=5),
        )


# ── end-to-end via get_chat_by_channel ────────────────────────────────────────


def _build_chat(downloader: FakeDownloader, **kwargs: Any) -> Any:
    return live_service.get_chat_by_channel(
        downloader,
        "examplechannel",
        _request(**kwargs.pop("request_kwargs", {})),
        **kwargs,
    )


def test_get_chat_by_channel_emits_preloaded_then_live() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )
    live_data = load_fixture("chat_message_event_data.json")
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]]
            ),
        )
        assert chat.title == "Example live stream title"
        assert chat.status == "live"
        messages = list(chat.chat)
        ids = [m["message_id"] for m in messages]
        assert ids == ["preloaded-1", "preloaded-2", "live-1"]


def test_get_chat_by_channel_dedups_live_against_preloaded() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )
    duplicate = {"id": "preloaded-1", "content": "dup", "type": "message"}
    fresh = {"id": "fresh", "content": "new", "type": "message"}
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [
                    [
                        pusher_frame(CHAT_MESSAGE_EVENT, duplicate),
                        pusher_frame(CHAT_MESSAGE_EVENT, fresh),
                    ]
                ]
            ),
        )
        ids = [m["message_id"] for m in chat.chat]
        assert ids == ["preloaded-1", "preloaded-2", "fresh"]


def test_get_chat_by_channel_filters_by_message_type() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    live_data = load_fixture("chat_message_event_data.json")
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_types": ["subscription"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator(
                [[pusher_frame(CHAT_MESSAGE_EVENT, live_data)]]
            ),
        )
        # text_message is filtered out; nothing should be emitted.
        assert list(chat.chat) == []


def test_get_chat_by_channel_reconnects_on_disconnect() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    frame_one = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "a", "content": "1"})
    frame_two = pusher_frame(CHAT_MESSAGE_EVENT, {"id": "b", "content": "2"})
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=factory,
            frame_iterator=make_frame_iterator(
                [[frame_one, ConnectionError("drop")], [frame_two]]
            ),
        )
        ids = [m["message_id"] for m in chat.chat]
        assert ids == ["a", "b"]
    assert len(created) == 2  # reconnected once
    assert created[0].close_count >= 1


def test_get_chat_by_channel_backfills_messages_missed_during_reconnect() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, load_fixture("preloaded_messages.json")),
        ]
    )

    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"message_groups": ["messages"]},
            transport_factory=FakeTransport,
            frame_iterator=make_frame_iterator([[ConnectionError("drop")], []]),
        )

        assert [message["message_id"] for message in chat.chat] == [
            "preloaded-1",
            "preloaded-2",
        ]


def test_get_chat_by_channel_repeated_disconnects_exhaust_budget() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession(
        [
            FakeResponse(200, load_fixture("channel_live.json")),
            FakeResponse(200, {"data": {"messages": []}}),
            FakeResponse(200, {"data": {"messages": []}}),
        ]
    )
    created: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport()
        created.append(transport)
        return transport

    with (
        patch(
            "chat_downloader.sites.kick.api_client._get_kick_session",
            return_value=session,
        ),
        pytest.raises(RetriesExceeded),
    ):
        chat = _build_chat(
            downloader,
            request_kwargs={"max_attempts": 2},
            transport_factory=factory,
            frame_iterator=make_frame_iterator(
                [
                    [ConnectionError("drop one")],
                    [ConnectionError("drop two")],
                ]
            ),
        )
        list(chat.chat)

    assert len(created) == 2
    assert all(transport.close_count >= 1 for transport in created)


def test_get_chat_by_channel_offline_succeeds_with_offline_title() -> None:
    downloader = FakeDownloader()
    session = FakeKickSession([FakeResponse(200, load_fixture("channel_offline.json"))])
    with patch(
        "chat_downloader.sites.kick.api_client._get_kick_session",
        return_value=session,
    ):
        chat = live_service.get_chat_by_channel(
            downloader, "examplechannel", _request()
        )
        assert chat.title == "examplechannel"
        assert chat.status == "idle"

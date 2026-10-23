# SPDX-License-Identifier: MIT

"""Exercise timed cancellation through the assembled Twitch IRC pipeline."""

from __future__ import annotations

import socket
from threading import Event
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.twitch.live_service import (
    _TwitchLiveIterator,
    iter_stream_chat_messages,
)
from chat_downloader.utils.timed_generator import TimedGenerator


@pytest.mark.parametrize("option", ["timeout", "inactivity_timeout", "close"])
@pytest.mark.parametrize("limit", [None, 1])
def test_twitch_deadline_interrupts_receive_and_closes_without_reconnect(
    monkeypatch, option, limit
):
    entered, released = Event(), Event()
    transport = Mock()

    def recv(_size):
        entered.set()
        assert released.wait(5)
        return b""

    transport.recv.side_effect = recv
    transport.shutdown.side_effect = lambda _how: released.set()
    factory = Mock(return_value=transport)
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_transport._create_irc_socket", factory
    )
    site = TwitchChatDownloader(proxy="")
    monkeypatch.setattr(
        site,
        "_download_gql",
        lambda *_args, **_kw: [{"data": {"user": {"id": "1", "stream": None}}}],
    )
    monkeypatch.setattr(site, "_update_badge_info", lambda *_: None)
    request = ChatRequest(
        max_messages=limit,
        **({"timeout": 60} if option == "close" else {option: 0.1}),
    ).resolved_for_site(site)
    chat = site.get_chat_by_stream_id("example", request)
    configure_chat(chat, request, site)
    try:
        assert entered.wait(2)
        if option == "close":
            chat.close()
        else:
            assert list(chat) == []
        chat.chat._worker.join(2)
        assert not chat.chat._worker.is_alive()
        transport.shutdown.assert_any_call(socket.SHUT_RDWR)
        transport.close.assert_called_once()
        transport.recv.assert_called_once()
        factory.assert_called_once()
        assert chat.diagnostics["reconnect_count"] == 0
        assert list(chat) == []
    finally:
        released.set()
        chat.close()
        chat.chat._worker.join(2)
        site.close()


@pytest.mark.parametrize("failure", [False, True])
def test_cancellation_during_irc_setup_closes_late_connection(failure):
    entered, released = Event(), Event()
    irc = Mock()
    site = Mock(_http_timeout=(10, 30), session=None)
    control = _TwitchLiveIterator()

    def connect(**_kwargs):
        entered.set()
        assert released.wait(5)
        if failure:
            raise OSError("setup failed after cancellation")
        return irc

    control.source = iter_stream_chat_messages(
        site,
        "example",
        ChatRequest(),
        irc_factory=connect,
        control=control,
    )
    timed = TimedGenerator(control, timeout=0.05)
    try:
        assert entered.wait(2)
        assert list(timed) == []
    finally:
        control.request_stop()
        released.set()
        timed._worker.join(2)
        timed.close()
    assert not timed._worker.is_alive()
    irc.join_channel.assert_not_called()
    site.retry.assert_not_called()
    assert irc.close_connection.call_count == (0 if failure else 1)


def test_closing_unstarted_twitch_iterator_prevents_setup():
    site = Mock()
    factory = Mock()
    control = _TwitchLiveIterator()
    control.source = iter_stream_chat_messages(
        site,
        "example",
        ChatRequest(),
        irc_factory=factory,
        control=control,
    )
    control.close()
    assert list(control) == []
    factory.assert_not_called()

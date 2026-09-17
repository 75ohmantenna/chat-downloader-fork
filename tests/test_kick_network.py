# SPDX-License-Identifier: MIT
"""Opt-in live Kick checks (--run-network); unexpected protocol failures fail."""

from __future__ import annotations

import os

import pytest
from websocket import WebSocketBadStatusException

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    RetriesExceeded,
    UserNotFound,
)
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.sites.kick.live_service import (
    _fetch_channel_with_retry,
    _open_subscribed_transport,
    _resolve_channel,
    _resolve_ws_proxy,
)
from chat_downloader.sites.kick.websocket_transport import KickPusherTransport
from tests.kick_helpers import request

pytestmark = [pytest.mark.network, pytest.mark.network_live, pytest.mark.timeout(45)]
# Override with a currently live public channel.
_CHANNEL = os.environ.get("KICK_TEST_CHANNEL", "xqc")


def _caused_by_websocket_http(error, status):
    while error is not None:
        if (
            isinstance(error, WebSocketBadStatusException)
            and error.status_code == status
        ):
            return True
        error = error.__cause__
    return False


def test_live_channel_connects_and_subscribes():
    downloader, transport = KickChatDownloader(), None
    options = request(
        url=f"https://kick.com/{_CHANNEL}", max_attempts=1, message_receive_timeout=1
    )
    try:
        channel = _fetch_channel_with_retry(downloader, _CHANNEL, options)
        channel_id, chatroom_id, title = _resolve_channel(channel, _CHANNEL)
        assert channel_id.isdigit()
        assert chatroom_id.isdigit()
        assert title
        transport = _open_subscribed_transport(
            downloader,
            chatroom_id,
            options,
            KickPusherTransport,
            proxy_url=_resolve_ws_proxy(downloader),
        )
    except CaptchaChallengeRequired as error:
        pytest.skip(f"Kick challenge block: try a fresh VPN/proxy endpoint. ({error})")
    except RetriesExceeded as error:
        if _caused_by_websocket_http(error, 403):
            pytest.skip("Kick Pusher HTTP 403: try a different network or --proxy.")
        raise
    except UserNotFound:
        pytest.skip(f"Kick channel {_CHANNEL!r} not found; set KICK_TEST_CHANNEL.")
    finally:
        if transport is not None:
            transport.close()
        downloader.close()

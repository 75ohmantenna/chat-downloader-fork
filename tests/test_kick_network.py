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
from chat_downloader.sites.kick.live_service import get_chat_by_channel
from tests.kick_helpers import assert_live_delivery, request

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


def test_live_channel_receives_confirmed_realtime_delivery():
    downloader = KickChatDownloader(connect_timeout=5, read_timeout=5)
    chat = None
    options = request(
        url=f"https://kick.com/{_CHANNEL}",
        max_attempts=1,
        message_receive_timeout=1,
        message_groups=["messages"],
    )
    try:
        # No injected factory: use the production negotiated KickPublicTransport.
        chat = get_chat_by_channel(downloader, _CHANNEL, options)
        assert chat.id == _CHANNEL
        assert chat.title
        assert_live_delivery(chat)
    except CaptchaChallengeRequired as error:
        pytest.skip(f"Kick challenge block: try a fresh VPN/proxy endpoint. ({error})")
    except RetriesExceeded as error:
        if _caused_by_websocket_http(error, 403):
            pytest.skip("Kick realtime HTTP 403: try a different network or --proxy.")
        raise
    except UserNotFound:
        pytest.skip(f"Kick channel {_CHANNEL!r} not found; set KICK_TEST_CHANNEL.")
    finally:
        if chat is not None:
            chat.close()
        downloader.close()

# SPDX-License-Identifier: MIT
"""Opt-in live Kick checks (--run-network); unexpected protocol failures fail."""

from __future__ import annotations

import os
import re

import pytest
from websocket import WebSocketBadStatusException

from chat_downloader.errors import CaptchaChallengeRequired, RetriesExceeded
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.sites.kick.live_service import get_chat_by_channel
from tests.kick_helpers import assert_live_delivery, request

pytestmark = [pytest.mark.network, pytest.mark.network_live, pytest.mark.timeout(45)]


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
    channel = os.environ.get("KICK_TEST_CHANNEL")
    if not channel:
        pytest.skip("Set KICK_TEST_CHANNEL to a currently active public Kick channel.")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", channel) is None:
        pytest.fail("KICK_TEST_CHANNEL must be a Kick channel slug.", pytrace=False)
    downloader = KickChatDownloader(connect_timeout=5, read_timeout=5)
    chat = None
    options = request(
        url=f"https://kick.com/{channel}",
        max_attempts=1,
        message_receive_timeout=1,
        message_groups=["messages"],
    )
    try:
        # No injected factory: use the production negotiated KickPublicTransport.
        chat = get_chat_by_channel(downloader, channel, options)
        assert chat.id == channel
        assert chat.title
        assert_live_delivery(chat)
    except CaptchaChallengeRequired as error:
        pytest.skip(f"Kick challenge block: try a fresh VPN/proxy endpoint. ({error})")
    except RetriesExceeded as error:
        if _caused_by_websocket_http(error, 403):
            pytest.skip("Kick realtime HTTP 403: try a different network.")
        raise
    finally:
        if chat is not None:
            chat.close()
        downloader.close()

# SPDX-License-Identifier: MIT

"""Opt-in live Kick checks.

These require outbound network access and a public channel. They are skipped by
default; run with ``--run-network``. Only explicit environmental failures
(challenge responses, blocked handshakes, or a missing override channel) are
skipped; unexpected Kick protocol errors fail.
"""

from __future__ import annotations

import os

import pytest
from websocket import WebSocketBadStatusException

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    RetriesExceeded,
    UserNotFound,
)
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.sites.kick.live_service import (
    _fetch_channel_with_retry,
    _open_subscribed_transport,
    _resolve_channel,
    _resolve_ws_proxy,
)
from chat_downloader.sites.kick.websocket_transport import KickPusherTransport

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_live,
    pytest.mark.timeout(45),
]

# Override with KICK_TEST_CHANNEL to point at a channel that is live right now.
_CHANNEL = os.environ.get("KICK_TEST_CHANNEL", "xqc")


def _caused_by_websocket_http(error: BaseException, status: int) -> bool:
    """Return whether an exception chain contains the given handshake status."""
    current: BaseException | None = error
    while current is not None:
        if (
            isinstance(current, WebSocketBadStatusException)
            and current.status_code == status
        ):
            return True
        current = current.__cause__
    return False


def test_live_channel_connects_and_subscribes() -> None:
    """Resolve channel metadata and complete a Pusher subscription handshake."""
    downloader = KickChatDownloader()
    request = ChatRequest(
        url=f"https://kick.com/{_CHANNEL}",
        max_attempts=1,
        message_receive_timeout=1,
    )
    transport = None
    try:
        channel = _fetch_channel_with_retry(downloader, _CHANNEL, request)
        channel_id, chatroom_id, title = _resolve_channel(channel, _CHANNEL)
        assert channel_id.isdigit()
        assert chatroom_id.isdigit()
        assert title

        transport = _open_subscribed_transport(
            downloader,
            chatroom_id,
            request,
            KickPusherTransport,
            proxy_url=_resolve_ws_proxy(downloader),
        )
    except CaptchaChallengeRequired as error:
        pytest.skip(
            "Kick returned a Cloudflare/challenge block — likely VPN/proxy "
            f"endpoint reputation or rate limiting. Try a fresh endpoint. ({error})"
        )
    except RetriesExceeded as error:
        if _caused_by_websocket_http(error, 403):
            pytest.skip(
                "Kick Pusher rejected this runner IP with HTTP 403; "
                "retry through a different network or --proxy."
            )
        raise
    except UserNotFound:
        pytest.skip(f"Kick channel {_CHANNEL!r} not found; set KICK_TEST_CHANNEL.")
    finally:
        if transport is not None:
            transport.close()
        downloader.close()

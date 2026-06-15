# SPDX-License-Identifier: MIT

"""Kick Pusher websocket transport.

This is the *only* module that imports ``websocket-client``. It exposes a
small, parsing-free interface (connect / subscribe / recv / pong / close) so
the live-chat orchestration in :mod:`chat_downloader.sites.kick.live_service`
can be unit-tested with an injected fake connector and never needs live access.

The dependency is isolated here deliberately so it can be swapped without
touching orchestration or parsing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from websocket import (
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
    create_connection,
)

from chat_downloader.debugging import logger

from .constants import (
    CHATROOM_CHANNEL_TEMPLATE,
    PUSHER_PING,
    PUSHER_PONG,
    PUSHER_SUBSCRIBE,
    PUSHER_WS_URL,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


def _default_connector(url: str, timeout: float | None) -> Any:
    """Open a real Pusher websocket connection.

    Args:
        url: The websocket URL to connect to.
        timeout: Socket timeout in seconds, or ``None`` to block.

    Returns:
        A connected ``websocket.WebSocket`` instance.
    """
    return create_connection(url, timeout=timeout)


class KickPusherTransport:
    """Manage a Kick Pusher websocket connection (framing/IO only)."""

    def __init__(
        self,
        *,
        connector: Callable[[str, float | None], Any] | None = None,
        url: str = PUSHER_WS_URL,
    ) -> None:
        """Initialize the transport.

        Args:
            connector: Callable that opens a websocket given ``(url, timeout)``.
                Defaults to a real ``websocket-client`` connection; tests inject
                a fake.
            url: Pusher websocket URL to connect to.
        """
        self._connector = connector or _default_connector
        self._url = url
        self._ws: Any = None

    def connect(self, timeout: float | None) -> None:
        """Open the websocket connection.

        Args:
            timeout: Initial socket timeout in seconds, or ``None`` to block.

        Raises:
            ConnectionError: If the underlying connection attempt fails.
        """
        try:
            self._ws = self._connector(self._url, timeout)
        except (WebSocketException, OSError) as error:
            msg = "Unable to open Kick websocket connection."
            raise ConnectionError(msg) from error

    def set_timeout(self, timeout: float | None) -> None:
        """Set the receive timeout on the open socket.

        Args:
            timeout: Timeout in seconds, or ``None`` to block.
        """
        if self._ws is not None:
            self._ws.settimeout(timeout)

    def _send(self, payload: dict[str, Any]) -> None:
        """Serialize and send a Pusher frame.

        Args:
            payload: The frame to JSON-encode and send.

        Raises:
            ConnectionError: If the send fails.
        """
        if self._ws is None:
            msg = "Kick websocket is not connected."
            raise ConnectionError(msg)
        try:
            self._ws.send(json.dumps(payload))
        except (WebSocketException, OSError) as error:
            msg = "Lost connection while sending to Kick websocket."
            raise ConnectionError(msg) from error

    def subscribe(self, chatroom_id: str) -> None:
        """Subscribe to a public chatroom channel.

        Args:
            chatroom_id: The numeric chatroom id to subscribe to.
        """
        channel = CHATROOM_CHANNEL_TEMPLATE.format(chatroom_id=chatroom_id)
        self._send(
            {"event": PUSHER_SUBSCRIBE, "data": {"auth": "", "channel": channel}}
        )

    def send_pong(self) -> None:
        """Reply to a Pusher ping to keep the connection alive."""
        self._send({"event": PUSHER_PONG, "data": {}})

    def recv(self) -> dict[str, Any] | None:
        """Receive and decode the next Pusher frame.

        Returns:
            The decoded frame as a dict, or ``None`` when the receive timed out
            or the frame was malformed (both are skipped by the caller).

        Raises:
            ConnectionError: If the connection is closed by the server.
        """
        if self._ws is None:
            msg = "Kick websocket is not connected."
            raise ConnectionError(msg)
        try:
            raw = self._ws.recv()
        except (TimeoutError, WebSocketTimeoutException):
            return None
        except (WebSocketConnectionClosedException, OSError) as error:
            msg = "Kick websocket connection closed."
            raise ConnectionError(msg) from error

        if not raw:
            msg = "Kick websocket connection closed."
            raise ConnectionError(msg)

        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Discarding malformed Kick websocket frame.")
            return None
        if not isinstance(frame, dict):
            logger.debug("Discarding non-object Kick websocket frame.")
            return None
        return frame

    def close(self) -> None:
        """Close the websocket connection, ignoring errors."""
        if self._ws is None:
            return
        try:
            self._ws.close()
        except (WebSocketException, OSError):
            pass
        finally:
            self._ws = None


def read_frames(
    transport: KickPusherTransport,
) -> Generator[dict[str, Any], None, None]:
    """Yield decoded Pusher frames, replying to pings transparently.

    This open-ended generator drives the live receive loop. It is separated
    from orchestration so tests can inject a finite fake in its place.

    Args:
        transport: A connected transport to read from.

    Yields:
        Decoded Pusher frames, excluding pings (answered in place) and
        timed-out/malformed reads (skipped).

    Raises:
        ConnectionError: If the connection is closed (drives reconnect).
    """
    while True:
        frame = transport.recv()
        if frame is None:
            continue
        if frame.get("event") == PUSHER_PING:
            transport.send_pong()
            continue
        yield frame

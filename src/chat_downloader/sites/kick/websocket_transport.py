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
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlparse

from websocket import (
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
    create_connection,
)

from chat_downloader.debugging import logger
from chat_downloader.sites.proxy import open_proxied_tls_socket

from .constants import (
    CHATROOM_CHANNEL_TEMPLATE,
    PUSHER_PING,
    PUSHER_PONG,
    PUSHER_SUBSCRIBE,
)
from .pusher_discovery import _HttpClient, get_pusher_ws_url

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.utils.json_types import JSONDict


class _WebSocketConnection(Protocol):
    """Minimal shape of a connected websocket object."""

    def settimeout(self, timeout: float | None) -> None: ...
    def send(self, data: str) -> None: ...
    def recv(self) -> str | bytes | None: ...
    def close(self) -> None: ...


class _PusherConnector(Protocol):
    """Callable that opens a websocket connection for the Kick transport."""

    def __call__(
        self,
        url: str,
        timeout: float | None,
        *,
        proxy_url: str | None = None,
    ) -> _WebSocketConnection | None: ...


_IDLE_WATCHDOG_SECONDS = 180.0
_MIN_RECEIVE_TIMEOUT_SECONDS = 1.0


def _default_connector(
    url: str,
    timeout: float | None,
    *,
    proxy_url: str | None = None,
) -> _WebSocketConnection:
    """Open a real Pusher websocket connection.

    Args:
        url: The websocket URL to connect to.
        timeout: Socket timeout in seconds, or ``None`` to block.
        proxy_url: Optional HTTP, HTTPS, or SOCKS proxy URL.

    Returns:
        A connected ``websocket.WebSocket`` instance.
    """
    socket_timeout = 10.0 if timeout is None else timeout
    proxy_socket = None
    if proxy_url is not None:
        parsed = urlparse(url)
        if parsed.scheme != "wss" or parsed.hostname is None:
            msg = f"Unsupported proxied websocket URL: {url!r}"
            raise OSError(msg)
        proxy_socket = open_proxied_tls_socket(
            parsed.hostname,
            parsed.port or 443,
            timeout=socket_timeout,
            proxy_url=proxy_url,
        )
    try:
        connection_options = {"socket": proxy_socket} if proxy_socket else {}
        return cast(
            "_WebSocketConnection",
            create_connection(
                url,
                timeout=timeout,
                **connection_options,
            ),
        )
    except BaseException:
        if proxy_socket is not None:
            with suppress(OSError):
                proxy_socket.close()
        raise


class KickPusherTransport:
    """Manage a Kick Pusher websocket connection (framing/IO only)."""

    def __init__(
        self,
        *,
        connector: _PusherConnector | None = None,
        url: str | None = None,
        proxy_url: str | None = None,
        pusher_http_client: _HttpClient | None = None,
    ) -> None:
        """Initialize the transport.

        Args:
            connector: Callable that opens a websocket given ``(url, timeout)``
                and an optional ``proxy_url`` keyword argument.
                Defaults to a real ``websocket-client`` connection; tests inject
                a fake.
            url: Pusher websocket URL to connect to. Defaults to the auto-
                discovered Pusher URL from Kick's JS bundle.
            proxy_url: Optional HTTP, HTTPS, or SOCKS proxy URL.
            pusher_http_client: HTTP client used to discover the Pusher key.
        """
        self._connector = connector or _default_connector
        self._url = url
        self._proxy_url = proxy_url
        self._pusher_http_client = pusher_http_client
        self._ws: _WebSocketConnection | None = None

    def connect(self, timeout: float | None) -> None:
        """Open the websocket connection.

        Args:
            timeout: Initial socket timeout in seconds, or ``None`` to block.

        Raises:
            ConnectionError: If the underlying connection attempt fails.
        """
        self.close()
        try:
            url = self._url or get_pusher_ws_url(
                http_client=self._pusher_http_client,
            )
            websocket = self._connector(
                url,
                timeout,
                proxy_url=self._proxy_url,
            )
        except (WebSocketException, OSError) as error:
            msg = "Unable to open Kick websocket connection."
            raise ConnectionError(msg) from error
        if websocket is None:
            msg = "Kick websocket connector returned no connection."
            raise ConnectionError(msg)
        self._ws = websocket

    def set_timeout(self, timeout: float | None) -> None:
        """Set the receive timeout on the open socket.

        Args:
            timeout: Timeout in seconds, or ``None`` to block.
        """
        if self._ws is not None:
            try:
                self._ws.settimeout(timeout)
            except (WebSocketException, OSError) as error:
                msg = "Unable to configure Kick websocket timeout."
                raise ConnectionError(msg) from error

    def _send(self, payload: JSONDict) -> None:
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

    def recv(self) -> JSONDict | None:
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
        except (
            WebSocketConnectionClosedException,
            WebSocketException,
            OSError,
        ) as error:
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
        return cast("JSONDict", frame)

    def close(self) -> None:
        """Close the websocket connection, ignoring errors."""
        if self._ws is None:
            return
        try:
            self._ws.close()
        except (WebSocketException, OSError) as error:
            logger.debug("Error closing Kick websocket: %s", error)
        finally:
            self._ws = None


def read_frames(
    transport: KickPusherTransport,
    *,
    idle_timeout: float = _IDLE_WATCHDOG_SECONDS,
) -> Generator[JSONDict, None, None]:
    """Yield decoded Pusher frames, replying to pings transparently.

    This open-ended generator drives the live receive loop. It is separated
    from orchestration so tests can inject a finite fake in its place.

    Args:
        transport: A connected transport to read from.
        idle_timeout: Maximum seconds without a decoded frame before the
            connection is treated as stale.

    Yields:
        Decoded Pusher frames, excluding pings (answered in place) and
        timed-out/malformed reads (skipped).

    Raises:
        ConnectionError: If the connection is closed (drives reconnect).
    """
    last_activity = time.monotonic()
    while True:
        frame = transport.recv()
        if frame is None:
            if time.monotonic() - last_activity >= idle_timeout:
                logger.debug(
                    "Kick websocket idle watchdog expired after %ss; reconnecting.",
                    idle_timeout,
                )
                msg = "Kick websocket connection became idle."
                raise ConnectionError(msg)
            continue
        last_activity = time.monotonic()
        if frame.get("event") == PUSHER_PING:
            transport.send_pong()
            continue
        yield frame

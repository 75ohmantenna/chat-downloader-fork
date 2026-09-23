# SPDX-License-Identifier: MIT

"""Kick Pusher framing/IO: connect, subscribe, recv, pong, and close.

Only this module imports ``websocket-client``; isolation lets live_service use
fake connectors offline and swap dependencies without changing parsing or orchestration.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, TypedDict, cast
from urllib.parse import urlparse

from websocket import (
    WebSocketBadStatusException,
    WebSocketConnectionClosedException,
    WebSocketException,
    WebSocketTimeoutException,
    create_connection,
)

from chat_downloader.debugging import logger
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.proxy import open_proxied_tls_socket

from .constants import (
    CHATROOM_CHANNEL_TEMPLATE,
    KICK_DEBUG_SAMPLE_LIMIT,
    PUSHER_PING,
    PUSHER_PONG,
    PUSHER_SUBSCRIBE,
)
from .pusher_discovery import _HttpClient, get_pusher_ws_url

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.sites.proxy import _ProxySocket
    from chat_downloader.utils.json_types import JSONDict


class _ConnectionOptions(TypedDict, total=False):
    """Optional socket supplied to the WebSocket connection factory."""

    socket: _ProxySocket


class _WebSocketConnection(Protocol):
    """Minimal shape of a connected WebSocket object."""

    def settimeout(self, timeout: float | None) -> None: ...
    def send(self, data: str) -> None: ...
    def recv(self) -> str | bytes | None: ...
    def close(self) -> None: ...


class _PusherConnector(Protocol):
    """Callable that opens a WebSocket connection for the Kick transport."""

    def __call__(
        self,
        url: str,
        timeout: float | None,
        *,
        proxy_url: str | None = None,
    ) -> _WebSocketConnection | None: ...


_IDLE_WATCHDOG_SECONDS = 180.0
_MIN_RECEIVE_TIMEOUT_SECONDS = 1.0


def _connect_error_message(error: WebSocketException | OSError) -> str:
    """Return an actionable, credential-free WebSocket connection error."""
    if isinstance(error, WebSocketBadStatusException):
        status = error.status_code
        hint = ""
        if status == 403:
            hint = " The endpoint may be blocking this IP; try --proxy."
        return f"Kick WebSocket handshake returned HTTP {status}.{hint}"
    return f"Unable to open Kick WebSocket connection ({type(error).__name__})."


def _default_connector(
    url: str,
    timeout: float | None,
    *,
    proxy_url: str | None = None,
) -> _WebSocketConnection:
    """Open a real ``websocket.WebSocket`` connection.

    Args:
        url: WebSocket endpoint URL to connect to.
        timeout: Socket timeout in seconds, or ``None`` to block.
        proxy_url: Optional HTTP, HTTPS, or SOCKS proxy URL.
    """
    socket_timeout = 10.0 if timeout is None else timeout
    parsed = urlparse(url)
    if parsed.scheme != "wss" or parsed.hostname is None:
        msg = f"Unsupported WebSocket URL: {url!r}"
        raise OSError(msg)
    proxy_socket = open_proxied_tls_socket(
        parsed.hostname,
        parsed.port or 443,
        timeout=socket_timeout,
        proxy_url=proxy_url,
    )
    try:
        connection_options: _ConnectionOptions = {"socket": proxy_socket}
        return cast(
            "_WebSocketConnection",
            create_connection(
                url,
                timeout=timeout,
                **connection_options,
            ),
        )
    except BaseException:
        with suppress(OSError):
            proxy_socket.close()
        raise


class KickPusherTransport:
    """Manage a Kick Pusher WebSocket connection (framing/IO only)."""

    def __init__(
        self,
        *,
        connector: _PusherConnector | None = None,
        url: str | None = None,
        proxy_url: str | None = None,
        pusher_http_client: _HttpClient | None = None,
        diagnostic_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the transport.

        Args:
            connector: Opens ``(url, timeout, proxy_url=...)``; defaults to
                ``websocket-client``. Tests may inject a fake.
            url: Defaults to the cached or compiled-in Pusher URL.
            proxy_url: Optional HTTP, HTTPS, or SOCKS proxy URL.
            pusher_http_client: HTTP client used to discover the Pusher key.
            diagnostic_callback: Optional counter callback for malformed frames.
        """
        self._connector = connector or _default_connector
        self._url = url
        self._proxy_url = proxy_url
        self._pusher_http_client = pusher_http_client
        self._diagnostic_callback = diagnostic_callback
        self._ws: _WebSocketConnection | None = None

    def _record_diagnostic(self, name: str) -> None:
        """Record a transport diagnostic when a callback is configured."""
        if self._diagnostic_callback is not None:
            self._diagnostic_callback(name)

    def connect(
        self,
        timeout: float | None,
        *,
        force_discover: bool = False,
    ) -> None:
        """Open the WebSocket connection.

        Args:
            timeout: Initial socket timeout in seconds, or ``None`` to block.
            force_discover: Refresh the cached Pusher key before connecting.

        Raises:
            ConnectionError: If the underlying connection attempt fails.
        """
        self.close()
        try:
            url = self._url or get_pusher_ws_url(
                force_discover=force_discover,
                http_client=self._pusher_http_client,
            )
            websocket = self._connector(
                url,
                timeout,
                proxy_url=self._proxy_url,
            )
        except (WebSocketException, OSError) as error:
            msg = _connect_error_message(error)
            raise ConnectionError(msg) from error
        if websocket is None:
            msg = "Kick WebSocket connector returned no connection."
            raise ConnectionError(msg)
        self._ws = websocket

    def set_timeout(self, timeout: float | None) -> None:
        """Set the open socket's receive timeout in seconds; ``None`` blocks."""
        if self._ws is not None:
            try:
                self._ws.settimeout(timeout)
            except (WebSocketException, OSError) as error:
                msg = "Unable to configure Kick WebSocket timeout."
                raise ConnectionError(msg) from error

    def _send(self, payload: JSONDict) -> None:
        """JSON-encode and send a Pusher frame.

        Raises:
            ConnectionError: If the send fails.
        """
        if self._ws is None:
            msg = "Kick WebSocket is not connected."
            raise ConnectionError(msg)
        try:
            self._ws.send(json.dumps(payload))
        except (WebSocketException, OSError) as error:
            msg = "Lost connection while sending to Kick WebSocket."
            raise ConnectionError(msg) from error

    def subscribe(self, chatroom_id: str) -> None:
        """Subscribe to a public channel by numeric chatroom ID."""
        channel = CHATROOM_CHANNEL_TEMPLATE.format(chatroom_id=chatroom_id)
        self._send(
            {"event": PUSHER_SUBSCRIBE, "data": {"auth": "", "channel": channel}}
        )

    def send_pong(self) -> None:
        """Reply to a Pusher ping to keep the connection alive."""
        self._send({"event": PUSHER_PONG, "data": {}})

    def recv(self) -> JSONDict | None:
        """Decode the next Pusher frame; return ``None`` on timeout or malformed input.

        Callers skip both timeout and malformed reads.

        Raises:
            ConnectionError: If the connection is closed by the server.
        """
        if self._ws is None:
            msg = "Kick WebSocket is not connected."
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
            msg = "Kick WebSocket connection closed."
            raise ConnectionError(msg) from error

        if not raw:
            msg = "Kick WebSocket connection closed."
            raise ConnectionError(msg)

        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._record_diagnostic("invalid_websocket_frame_count")
            capture_debug_sample(
                "kick-unknown-websocket-shape",
                {"raw": raw, "reason": "invalid JSON"},
                sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
            )
            logger.debug("Discarding malformed Kick WebSocket frame.")
            return None
        if not isinstance(frame, dict):
            self._record_diagnostic("invalid_websocket_frame_count")
            capture_debug_sample(
                "kick-unknown-websocket-shape",
                {"raw": frame, "reason": "decoded frame was not an object"},
                sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
            )
            logger.debug("Discarding non-object Kick WebSocket frame.")
            return None
        return cast("JSONDict", frame)

    def close(self) -> None:
        """Close the WebSocket connection, ignoring errors."""
        if self._ws is None:
            return
        try:
            self._ws.close()
        except (WebSocketException, OSError) as error:
            logger.debug("Error closing Kick WebSocket: %s", error)
        finally:
            self._ws = None


def read_frames(
    transport: KickPusherTransport,
    *,
    idle_timeout: float = _IDLE_WATCHDOG_SECONDS,
) -> Generator[JSONDict, None, None]:
    """Yield frames from a connected transport, answering pings before yielding.

    This open-ended live loop is separate from orchestration for finite test fakes.
    Keepalive frames reach diagnostics; timed-out and malformed reads are skipped.

    Args:
        transport: Connected Pusher transport supplying frames and sending pongs.
        idle_timeout: Maximum seconds without a decoded frame before staleness.

    Raises:
        ConnectionError: If closed or stale (drives reconnect).
    """
    last_activity = time.monotonic()
    while True:
        frame = transport.recv()
        if frame is None:
            if time.monotonic() - last_activity >= idle_timeout:
                logger.debug(
                    "Kick WebSocket idle watchdog expired after %ss; reconnecting.",
                    idle_timeout,
                )
                msg = "Kick WebSocket connection became idle."
                raise ConnectionError(msg)
            continue
        last_activity = time.monotonic()
        if frame.get("event") == PUSHER_PING:
            transport.send_pong()
        yield frame

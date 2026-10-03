# SPDX-License-Identifier: MIT

"""Own independent public chat/channel connections and periodic REST snapshots."""

from __future__ import annotations

import time
from collections import deque
from contextlib import suppress
from functools import partial
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.debugging import logger
from chat_downloader.utils.json_types import get_str

from .centrifugo_transport import KickCentrifugoTransport
from .constants import PUSHER_SUBSCRIPTION_SUCCEEDED
from .errors import KickError, KickServerError
from .public_state import KickPublicState, category_feeds
from .realtime_connection import KickRealtimeClient
from .websocket_transport import KickPusherTransport, _default_connector, read_frames

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONDict, JSONList


class _NegotiatedPusherTransport(KickPusherTransport):
    """Require confirmation of every public subscription before its deadline."""

    def __init__(self) -> None:
        """Track pending public feed acknowledgements."""
        super().__init__()
        self._pending_subscriptions: dict[str, float] = {}

    def subscribe_channels(self, channels: list[str]) -> None:
        """Register deadlines before commands can be acknowledged."""
        self._pending_subscriptions.update(
            dict.fromkeys(channels, time.monotonic() + 10.0)
        )
        super().subscribe_channels(channels)

    def recv(self) -> JSONDict | None:
        """Reject a connection that silently fails to subscribe to any feed."""
        if any(
            time.monotonic() >= value for value in self._pending_subscriptions.values()
        ):
            msg = "Kick public Pusher subscription acknowledgement timed out."
            raise ConnectionError(msg)
        frame = super().recv()
        if frame is not None and frame.get("event") == PUSHER_SUBSCRIPTION_SUCCEEDED:
            self._pending_subscriptions.pop(get_str(frame, "channel"), None)
        return frame


class KickPublicTransport(KickPusherTransport):
    """Multiplex the website's independent negotiated transports with backpressure."""

    def __init__(
        self,
        *,
        username: str,
        channel_id: str,
        diagnostic_callback: Callable[[str], None] | None = None,
        trust_env: bool = True,
        http_timeout: tuple[float, float] = (10.0, 30.0),
    ) -> None:
        """Initialize ownership, bounded buffering, and snapshot state."""
        super().__init__(diagnostic_callback=diagnostic_callback)
        self.username = username
        self.channel_id = channel_id
        self._trust_env = trust_env
        self._http_timeout = http_timeout
        self._stopped = Event()
        self._queue: Queue[JSONDict | Exception] = Queue(maxsize=1024)
        self._workers: list[Thread] = []
        self._transports: list[KickPusherTransport] = []
        self._clients: list[KickRealtimeClient] = []
        self._state: KickPublicState | None = None
        self._channel: JSONDict = {}
        self._last_metadata: JSONDict | None = None
        self._last_viewers: JSONList | None = None
        self._next_poll = 0.0
        self._receive_timeout = 1.0
        self._pending: deque[JSONDict | Exception] = deque()

    def _put(self, item: JSONDict | Exception) -> None:
        while not self._stopped.is_set():
            try:
                self._queue.put(item, timeout=0.1)
            except Full:
                continue
            else:
                return

    def _read(
        self, transport: KickPusherTransport, client: KickRealtimeClient | None = None
    ) -> None:
        try:
            for frame in read_frames(transport):
                if self._stopped.is_set():
                    break
                if frame.get("event") == PUSHER_SUBSCRIPTION_SUCCEEDED:
                    self._record_diagnostic("public_subscription_count")
                self._put(frame)
        except Exception as error:  # noqa: BLE001 — transfer worker failures to the consumer
            # Surface worker failures to the consumer, including terminal access errors.
            self._put(error)
        finally:
            transport.close()
            if client is not None:
                client.close()

    def connect(self, timeout: float | None, *, force_discover: bool = False) -> None:
        """Negotiate chat and global descriptors separately, including regional URLs."""
        del force_discover
        try:
            proxy = {"https": self._proxy_url} if self._proxy_url else None
            # Explicit arguments keep transport/session ownership typed and isolated.
            self._state = KickPublicState(
                proxy=proxy, trust_env=self._trust_env, timeout=self._http_timeout
            )
            self._channel = self._state.metadata(self.username)
            for channel_id in (self.channel_id, None):
                client = KickRealtimeClient(
                    proxy=proxy, trust_env=self._trust_env, timeout=self._http_timeout
                )
                self._clients.append(client)
                connection = client.negotiate(channel_id)
                transport = (
                    KickCentrifugoTransport(client=client)
                    if connection.provider == "centrifugo"
                    else _NegotiatedPusherTransport()
                )
                transport._url = connection.url
                transport._proxy_url = self._proxy_url
                transport._connector = partial(
                    _default_connector, origin="https://kick.com"
                )
                transport._diagnostic_callback = self._diagnostic_callback
                self._transports.append(transport)
                transport.connect(timeout)
                self._record_diagnostic(f"{connection.provider}_connection_count")
        except (
            KickServerError,
            RequestException,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            self.close()
            msg = "Kick public realtime setup failed temporarily."
            raise ConnectionError(msg) from error
        except BaseException:
            self.close()
            raise

    def subscribe(self, chatroom_id: str) -> None:
        """Subscribe to public feeds using the website connection scopes."""
        self._transports[0].subscribe_channels(
            [
                f"chatrooms.{chatroom_id}.v2",
            ]
        )
        self._transports[1].subscribe_channels(
            [
                f"channel.{self.channel_id}",
                f"channel_{self.channel_id}",
                f"predictions-channel-{self.channel_id}",
                f"chatrooms.{chatroom_id}",
                f"chatroom_{chatroom_id}",
                *category_feeds(self._channel),
            ]
        )

    def set_timeout(self, timeout: float | None) -> None:
        """Start one bounded reader per connection after subscriptions are sent."""
        self._receive_timeout = min(timeout or 1.0, 1.0)
        for index, transport in enumerate(self._transports):
            transport.set_timeout(self._receive_timeout)
            worker = Thread(
                target=self._read, args=(transport, self._clients[index]), daemon=True
            )
            self._workers.append(worker)
            worker.start()

    def _poll(self) -> None:
        if self._state is None or time.monotonic() < self._next_poll:
            return
        self._next_poll = time.monotonic() + 60.0
        category_changed = False
        try:
            channel = self._state.metadata(self.username)
            metadata: JSONDict = {
                "livestream": channel.get("livestream"),
                "followers_count": channel.get("followers_count"),
                "chatroom": channel.get("chatroom"),
            }
            if metadata != self._last_metadata:
                self._pending.append(
                    {
                        "event": "kick:public_state",
                        "data": metadata,
                        "source": "public_rest",
                    }
                )
                self._last_metadata = metadata
            category_changed = category_feeds(channel) != category_feeds(self._channel)
            self._channel = channel
            self._record_diagnostic("public_state_poll_count")
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            self._record_diagnostic("public_state_poll_failure_count")
            logger.debug("Kick public metadata poll failed (%s).", type(error).__name__)
        try:
            viewers = self._state.viewers(self._channel)
            if viewers != self._last_viewers:
                self._pending.append(
                    {
                        "event": "kick:viewer_count",
                        "data": viewers,
                        "source": "public_rest",
                    }
                )
                self._last_viewers = viewers
        except (RequestException, OSError, KickError, ValueError, TypeError) as error:
            self._record_diagnostic("public_state_poll_failure_count")
            logger.debug("Kick public viewer poll failed (%s).", type(error).__name__)
        if category_changed:
            self._pending.append(ConnectionError("Kick public category feeds changed."))

    def recv(self) -> JSONDict | None:
        """Yield merged events without dropping buffered publications."""
        if self._stopped.is_set():
            msg = "Kick public transport was stopped."
            raise ConnectionError(msg)
        self._poll()
        if self._pending:
            item = self._pending.popleft()
            if isinstance(item, Exception):
                raise item
            return item
        try:
            item = self._queue.get(timeout=self._receive_timeout)
        except Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def send_pong(self) -> None:
        """Readers answer provider-specific keepalive before forwarding controls."""

    def request_stop(self) -> None:
        """Wake all socket readers and interrupt queue backpressure."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        with suppress(Full):
            self._queue.put_nowait(
                ConnectionError("Kick public transport was stopped.")
            )
        for transport in self._transports:
            transport.request_stop()

    def close(self) -> None:
        """Stop workers before releasing the sessions used for token renewal."""
        self.request_stop()
        for worker in self._workers:
            worker.join(timeout=0.2)
        for transport in self._transports:
            transport.close()
        for index, client in enumerate(self._clients):
            # Started readers own HTTP cleanup, including an in-flight renewal.
            if index >= len(self._workers):
                client.close()
        if self._state is not None:
            self._state.close()
            self._state = None

# SPDX-License-Identifier: MIT

"""Own independent public chat/channel connections and periodic REST snapshots."""

from __future__ import annotations

import time
from contextlib import suppress
from functools import partial
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.sites.proxy import resolve_session_proxy
from chat_downloader.utils.json_types import get_str

from .centrifugo_transport import KickCentrifugoTransport
from .constants import PUSHER_SUBSCRIPTION_SUCCEEDED
from .errors import KickServerError
from .public_state import KickPublicState, category_feeds
from .realtime_connection import KickRealtimeClient
from .websocket_transport import KickPusherTransport, _default_connector, read_frames

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONDict

    from .websocket_transport import _PusherConnector


_PUBLIC_STATE_POLL_INTERVAL = 60.0


class _NegotiatedPusherTransport(KickPusherTransport):
    """Require confirmation of every public subscription before its deadline."""

    def __init__(
        self,
        *,
        url: str | None = None,
        proxy_url: str | None = None,
        connector: _PusherConnector | None = None,
        diagnostic_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Track pending public feed acknowledgements."""
        super().__init__(
            url=url,
            proxy_url=proxy_url,
            connector=connector,
            diagnostic_callback=diagnostic_callback,
        )
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


class KickPublicTransport:
    """Multiplex the website's independent negotiated transports with backpressure."""

    def __init__(
        self,
        *,
        username: str,
        public_events: bool = True,
        channel_id: str,
        diagnostic_callback: Callable[[str], None] | None = None,
        trust_env: bool = True,
        http_timeout: tuple[float, float] = (10.0, 30.0),
        proxy: dict[str, str] | None = None,
    ) -> None:
        """Initialize ownership, bounded buffering, and snapshot state."""
        self._diagnostic_callback = diagnostic_callback
        self._public_events = public_events
        self.username = username
        self.channel_id = channel_id
        self._trust_env = trust_env
        self._http_timeout = http_timeout
        self._configured_proxy = dict(proxy) if proxy else None
        self._stopped = Event()
        self._reconnect_requested = Event()
        self._reader_errors: Queue[Exception] = Queue()
        self._queue: Queue[JSONDict | Exception] = Queue(maxsize=1024)
        self._pending: Queue[JSONDict] = Queue(maxsize=2)
        self._workers: list[Thread] = []
        self._poll_worker: Thread | None = None
        self._poll_stopped = Event()
        self._poll_lock = Lock()
        self._transports: list[KickPusherTransport] = []
        self._clients: list[KickRealtimeClient] = []
        self._state: KickPublicState | None = None
        self._channel: JSONDict = {}
        self._next_poll = 0.0
        self._receive_timeout = 1.0

    def _record_diagnostic(self, name: str) -> None:
        if self._diagnostic_callback is not None:
            self._diagnostic_callback(name)

    def _check_stopped(self) -> None:
        if self._stopped.is_set():
            msg = "Kick public transport was stopped."
            raise ConnectionError(msg)

    def _put(self, item: JSONDict | Exception) -> None:
        while not self._stopped.is_set():
            try:
                self._queue.put(item, timeout=0.1)
            except Full:
                continue
            else:
                return

    def _put_state(self, frame: JSONDict) -> None:
        while not self._poll_stopped.is_set():
            try:
                self._pending.put(frame, timeout=0.1)
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
                if self._reconnect_requested.is_set():
                    break
        except Exception as error:  # noqa: BLE001 — transfer worker failures to the consumer
            if not (
                self._reconnect_requested.is_set()
                and isinstance(error, ConnectionError)
            ):
                self._request_reconnect(error)
        finally:
            transport.close()
            if client is not None:
                client.close()

    def _request_reconnect(self, error: Exception) -> None:
        """Stop reads while allowing each accepted publication to finish queuing."""
        self._reader_errors.put_nowait(error)
        self._reconnect_requested.set()
        self._poll_stopped.set()
        for transport in self._transports:
            transport.request_stop()

    def connect(self, timeout: float | None, *, force_discover: bool = False) -> None:
        """Negotiate chat and global descriptors separately, including regional URLs."""
        del force_discover
        try:
            self._check_stopped()
            proxy = self._configured_proxy
            # Explicit arguments keep transport/session ownership typed and isolated.
            if self._public_events:
                state = KickPublicState(
                    proxy=proxy, trust_env=self._trust_env, timeout=self._http_timeout
                )
                try:
                    self._channel = state.metadata(self.username)
                finally:
                    state.close()
                self._record_diagnostic("public_state_poll_count")
                self._check_stopped()
            scopes = (
                (self.channel_id, None) if self._public_events else (self.channel_id,)
            )
            for channel_id in scopes:
                client = KickRealtimeClient(
                    proxy=proxy, trust_env=self._trust_env, timeout=self._http_timeout
                )
                self._clients.append(client)
                connection = client.negotiate(channel_id)
                self._check_stopped()
                proxy_url = resolve_session_proxy(
                    SimpleNamespace(proxies=proxy, trust_env=self._trust_env),
                    connection.url.replace("wss://", "https://", 1),
                )
                connector = partial(_default_connector, origin="https://kick.com")
                transport = (
                    KickCentrifugoTransport(
                        client=client,
                        url=connection.url,
                        proxy_url=proxy_url,
                        connector=connector,
                        diagnostic_callback=self._diagnostic_callback,
                    )
                    if connection.provider == "centrifugo"
                    else _NegotiatedPusherTransport(
                        url=connection.url,
                        proxy_url=proxy_url,
                        connector=connector,
                        diagnostic_callback=self._diagnostic_callback,
                    )
                )
                self._transports.append(transport)
                transport.connect(timeout)
                self._check_stopped()
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
        self._check_stopped()
        self._transports[0].subscribe_channels(
            [
                f"chatrooms.{chatroom_id}.v2",
            ]
        )
        self._check_stopped()
        if not self._public_events:
            return
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
        """Start socket readers and, for public events, one owned REST worker."""
        self._check_stopped()
        self._receive_timeout = min(timeout or 1.0, 1.0)
        for index, transport in enumerate(self._transports):
            transport.set_timeout(self._receive_timeout)
            self._check_stopped()
            if index < len(self._workers):
                continue
            worker = Thread(
                target=self._read, args=(transport, self._clients[index]), daemon=True
            )
            worker.start()
            self._workers.append(worker)
            self._check_stopped()
        with self._poll_lock:
            self._check_stopped()
            if self._public_events and self._poll_worker is None:
                worker = Thread(target=self._poll_loop, daemon=True)
                worker.start()
                self._poll_worker = worker
                self._check_stopped()

    def _poll_loop(self) -> None:
        """Create, use, and release a snapshot session on its worker thread."""
        state: KickPublicState | None = None
        try:
            if self._poll_stopped.is_set():
                return
            state = KickPublicState(
                proxy=self._configured_proxy,
                trust_env=self._trust_env,
                timeout=self._http_timeout,
            )
            self._state = state
            self._poll(initial=True)
            while not self._poll_stopped.wait(
                max(0.0, self._next_poll - time.monotonic())
            ):
                self._poll()
        except Exception as error:  # noqa: BLE001 — transfer construction failures
            if not self._stopped.is_set():
                self._request_reconnect(error)
        finally:
            if state is not None:
                state.close()

    def _poll(self, *, initial: bool = False) -> None:
        if (
            self._state is None
            or self._poll_stopped.is_set()
            or (not initial and time.monotonic() < self._next_poll)
        ):
            return
        self._next_poll = time.monotonic() + _PUBLIC_STATE_POLL_INTERVAL
        try:
            for frame in self._state.snapshots(
                self.username,
                self._poll_stopped,
                self._record_diagnostic,
                initial_channel=self._channel if initial else None,
            ):
                self._put_state(frame)
        except Exception as error:  # noqa: BLE001 — transfer worker failures to the consumer
            if not self._stopped.is_set():
                self._request_reconnect(error)

    def recv(self) -> JSONDict | None:
        """Yield merged events without dropping buffered publications."""
        self._check_stopped()
        with suppress(Empty):
            return self._pending.get_nowait()
        try:
            item = self._queue.get(timeout=self._receive_timeout)
        except Empty:
            if self._reconnect_requested.is_set() and not any(
                worker.is_alive() for worker in self._workers
            ):
                # A reader may publish and exit just after the timed get failed.
                try:
                    item = self._queue.get_nowait()
                except Empty:
                    item = self._reader_error()
            else:
                return None
        if isinstance(item, Exception):
            raise item
        return item

    def _reader_error(self) -> Exception:
        """Retain the terminal outcome, preferring access errors over reconnects."""
        error = self._reader_errors.get_nowait()
        while not self._reader_errors.empty():
            following = self._reader_errors.get_nowait()
            if isinstance(error, ConnectionError):
                error = following
        self._reader_errors.put_nowait(error)
        return error

    def send_pong(self) -> None:
        """Readers answer provider-specific keepalive before forwarding controls."""

    def request_stop(self) -> None:
        """Wake all socket readers and interrupt queue backpressure."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._poll_stopped.set()
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
        with self._poll_lock:
            poll_worker = self._poll_worker
        if poll_worker is not None:
            poll_worker.join(timeout=0.2)
        for transport in self._transports:
            transport.close()
        for index, client in enumerate(self._clients):
            # Started readers own HTTP cleanup, including an in-flight renewal.
            if index >= len(self._workers):
                client.close()

# SPDX-License-Identifier: MIT

"""Anonymous Centrifugo protocol with bounded acknowledgements and token refresh."""

from __future__ import annotations

import json
import time
from collections import deque
from typing import TYPE_CHECKING, cast

from chat_downloader.utils.json_types import (
    get_bool,
    get_dict,
    get_int,
    get_str,
)

from .constants import (
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_PING,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from .errors import KickRealtimeRejected, KickServerError
from .websocket_transport import KickPusherTransport

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

    from .realtime_connection import KickRealtimeClient


class KickCentrifugoTransport(KickPusherTransport):
    """Translate the website's JSON protocol to provider-independent event frames."""

    def __init__(self, *, client: KickRealtimeClient) -> None:
        """Initialize the anonymous protocol state and its owning client."""
        # Explicit transport options are set by the public connection owner.
        super().__init__()
        self.client = client
        self._commands: dict[int, tuple[str, str, float]] = {}
        self._next_id = 0
        self._frames: deque[JSONDict] = deque()
        self._refresh_at = float("inf")
        self._heartbeat_at = float("inf")
        self._heartbeat_seconds = 180.0
        self._pong_required = False
        self._ack_timeout = 10.0

    def _command(self, kind: str, data: JSONDict, channel: str = "") -> None:
        self._next_id += 1
        self._commands[self._next_id] = (
            kind,
            channel,
            time.monotonic() + self._ack_timeout,
        )
        self._send({"id": self._next_id, kind: data})

    def connect(self, timeout: float | None, *, force_discover: bool = False) -> None:
        """Open the socket and authenticate only its anonymous connection."""
        self._commands.clear()
        self._frames.clear()
        self._next_id = 0
        self._refresh_at = float("inf")
        self._heartbeat_seconds = 180.0
        self._pong_required = False
        super().connect(timeout, force_discover=force_discover)
        self._ack_timeout = max(timeout or 10.0, 1.0)
        self._heartbeat_at = time.monotonic() + self._heartbeat_seconds
        self._command(
            "connect",
            {
                "token": self.client.connection_token(),
                "name": "chat-downloader",
                "version": "1",
            },
        )

    def subscribe_channels(self, channels: list[str]) -> None:
        """Send one public subscription command per feed."""
        for channel in channels:
            self._command("subscribe", {"channel": channel}, channel)

    def send_pong(self) -> None:
        """Centrifugo ping is an empty JSON object, answered when requested."""
        if self._pong_required:
            self._send({})

    def _lease(self, result: JSONDict) -> None:
        if get_bool(result, "expires"):
            ttl = get_int(result, "ttl")
            if ttl < 1:
                msg = "Kick anonymous connection token has expired."
                raise ConnectionError(msg)
            self._refresh_at = time.monotonic() + max(ttl * 0.8, 0.1)

    def _reply(self, reply: JSONDict) -> JSONDict | None:
        command = self._commands.pop(get_int(reply, "id"), None)
        if command is None:
            msg = "Kick realtime returned an unexpected acknowledgement."
            raise ConnectionError(msg)
        kind, channel, _deadline = command
        error = get_dict(reply, "error")
        if error:
            if get_bool(error, "temporary"):
                msg = "Kick realtime returned a temporary command error."
                raise ConnectionError(msg)
            msg = "Kick realtime rejected a public connection command."
            raise KickRealtimeRejected(msg)
        result = reply.get(kind)
        if not isinstance(result, dict):
            msg = "Kick realtime acknowledgement omitted its result."
            raise ConnectionError(msg)
        if kind == "connect":
            self._lease(result)
            self._pong_required = get_bool(result, "pong")
            ping = get_int(result, "ping")
            self._heartbeat_seconds = ping + 10.0 if ping > 0 else 180.0
            self._heartbeat_at = time.monotonic() + self._heartbeat_seconds
            return {"event": PUSHER_CONNECTION_ESTABLISHED, "data": {}}
        if kind == "subscribe":
            return {
                "event": PUSHER_SUBSCRIPTION_SUCCEEDED,
                "channel": channel,
                "data": {},
            }
        self._lease(result)
        return None

    def _publication(self, push: JSONDict) -> JSONDict | None:
        if "disconnect" in push or "unsubscribe" in push:
            advice = get_dict(push, "disconnect") or get_dict(push, "unsubscribe")
            code = get_int(advice, "code")
            if 3500 <= code < 4000 or 4500 <= code < 5000:
                msg = "Kick realtime permanently closed a public feed."
                raise KickRealtimeRejected(msg)
            msg = "Kick realtime closed a public feed."
            raise ConnectionError(msg)
        publication = get_dict(push, "pub")
        if not publication:
            self._record_diagnostic("invalid_websocket_frame_count")
            return None
        data = publication.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict) or not get_str(data, "event"):
            self._record_diagnostic("invalid_websocket_frame_count")
            return None
        return {
            "event": get_str(data, "event"),
            "data": data.get("data"),
            "channel": get_str(push, "channel"),
        }

    def _decode(self, raw: str | bytes) -> None:
        for line in raw.splitlines():
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    self._record_diagnostic("invalid_websocket_frame_count")
                    continue
                if not item:
                    frame: JSONDict | None = {"event": PUSHER_PING, "data": {}}
                elif get_int(item, "id") > 0:
                    frame = self._reply(cast("JSONDict", item))
                else:
                    frame = self._publication(get_dict(item, "push"))
                if frame is not None:
                    self._frames.append(frame)
            except (ValueError, TypeError):
                # Do not sample wire frames: connect/refresh replies can contain tokens.
                self._record_diagnostic("invalid_websocket_frame_count")

    def recv(self) -> JSONDict | None:
        """Read batches, enforce command/heartbeat deadlines, and renew the lease."""
        if self._frames:
            return self._frames.popleft()
        now = time.monotonic()
        if now >= self._heartbeat_at or any(
            now >= deadline for _kind, _channel, deadline in self._commands.values()
        ):
            msg = "Kick realtime heartbeat or acknowledgement timed out."
            raise ConnectionError(msg)
        if now >= self._refresh_at:
            self._refresh_at = float("inf")
            try:
                token = self.client.connection_token()
            except KickServerError as error:
                msg = "Kick anonymous token renewal failed temporarily."
                raise ConnectionError(msg) from error
            self._command("refresh", {"token": token})
        raw = self.recv_raw()
        if raw is not None:
            self._heartbeat_at = time.monotonic() + self._heartbeat_seconds
            self._decode(raw)
        return self._frames.popleft() if self._frames else None

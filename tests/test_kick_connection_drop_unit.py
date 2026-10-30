# SPDX-License-Identifier: MIT

"""Real loopback WebSocket drops compose with recovery and verified writers."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from websocket import create_connection

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.kick import live_service
from chat_downloader.sites.kick.constants import (
    CHAT_MESSAGE_EVENT,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.websocket_transport import KickPusherTransport
from tests.kick_helpers import load_fixture, message_page, pusher_frame, raw_message


def _send(connection, payload):
    data = json.dumps(payload).encode()
    size = len(data)
    header = (
        bytes([0x81, size]) if size < 126 else b"\x81\x7e" + struct.pack("!H", size)
    )
    connection.sendall(header + data)


def _handshake(connection):
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = connection.recv(4096)
        assert chunk
        request += chunk
    key = next(
        line.split(b":", 1)[1].strip()
        for line in request.split(b"\r\n")
        if line.lower().startswith(b"sec-websocket-key:")
    )
    accept = base64.b64encode(
        hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
    )
    connection.sendall(
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
        b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
    )
    assert connection.recv(4096)  # Wait for the client's subscription.


@pytest.mark.parametrize("close_frame", [False, True])
def test_socket_drop_recovers_history_once_and_finalizes_verified_outputs(
    tmp_path, monkeypatch, close_frame
):
    first = raw_message("before", "2026-01-01T00:00:05Z", "before outage")
    missed = raw_message("missed", "2026-01-01T00:00:08Z", "during outage")
    last = raw_message("after", "2026-01-01T00:00:13Z", "after outage")
    for row in (first, missed, last):
        row["sender"] = {"id": 10, "username": "test"}
    reconnected = False
    recovered = False

    def serve(listener):
        nonlocal reconnected
        for index in range(2):
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                _handshake(connection)
                reconnected = bool(index)
                _send(
                    connection,
                    {
                        "event": PUSHER_SUBSCRIPTION_SUCCEEDED,
                        "channel": "chatrooms.54321.v2",
                    },
                )
                _send(
                    connection,
                    pusher_frame(CHAT_MESSAGE_EVENT, last if index else first),
                )
                if index == 0:
                    if close_frame:
                        connection.sendall(b"\x88\x02\x03\xe8")
                    connection.shutdown(socket.SHUT_WR)
                else:
                    connection.recv(4096)  # Owner must close the second connection.

    def history(*args, **kwargs):
        nonlocal recovered
        if reconnected and not recovered:
            recovered = True
            return message_page([last, missed, first])
        return message_page([])

    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as executor:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(8)
        address = f"ws://127.0.0.1:{listener.getsockname()[1]}/chat"
        server = executor.submit(serve, listener)

        class Downloader:
            _NAME = "kick.com"
            _http_timeout = (2, 2)

            def __init__(self, **kwargs):
                self._kick_client = Mock()
                self._kick_client.fetch_channel.return_value = load_fixture(
                    "channel_live.json"
                )
                self._kick_client.fetch_preloaded_chat_state.return_value = None
                self._kick_client.fetch_message_page.side_effect = history

            def get_chat(self, **kwargs):
                request = ChatRequest.from_kwargs(**kwargs)
                chat = live_service.get_chat_by_channel(
                    self,
                    "test",
                    request,
                    transport_factory=lambda: KickPusherTransport(
                        url=address,
                        connector=lambda *args, **kwargs: create_connection(
                            address, timeout=3, http_no_proxy=["127.0.0.1"]
                        ),
                    ),
                )
                configure_chat(chat, request, self)
                return chat

            def is_live_status(self, status):
                return status in {"live", "idle"}

            def resolve_live_format(self, name):
                return name

            def close(self):
                pass

        monkeypatch.setattr(live_service, "time", lambda: 1767225614.0)
        monkeypatch.setattr(live_service, "time_ns", lambda: 1767225614000000000)
        result = execute_run(
            Downloader,
            url="https://kick.com/test",
            max_messages=3,
            timeout=6,
            max_attempts=2,
            retry_timeout=0,
            interruptible_retry=False,
            output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
            format="kick",
            quiet=True,
            verify_output=True,
            run_manifest=str(tmp_path / "manifest.json"),
        )
        server.result(timeout=8)
    assert result.success
    assert result.parity_status == "passed"
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [row["message_id"] for row in rows] == ["before", "missed", "after"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    state = manifest["provider_diagnostics"]
    assert state["websocket_reconnect_count"] == 1
    assert state["reconnect_backfill_emitted_count"] == 2
    assert state["history_recovery_failure_count"] == 0
    assert manifest["provider_inspection"]["status"] == "ok"
    assert (
        manifest["provider_inspection"]["frame_accounting"]["unaccounted_frames"] == 0
    )

# SPDX-License-Identifier: MIT

"""Shared fakes and fixture loaders for Kick site tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chat_downloader.sites.retry import retry as _perform_retry

_FIXTURES = Path(__file__).parent / "fixtures" / "kick"


def load_fixture(name: str) -> Any:
    """Load and decode a JSON fixture from ``tests/fixtures/kick``."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str) -> str:
    """Load a raw text fixture from ``tests/fixtures/kick``."""
    return (_FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        *,
        text: str | None = None,
        content_type: str = "application/json",
        malformed: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": content_type}
        if text is None:
            text = json.dumps(payload) if payload is not None else ""
        self.text = text
        self._malformed = malformed

    def json(self) -> Any:
        if self._malformed:
            raise json.JSONDecodeError("bad json", self.text or "", 0)
        return self._payload


class FakeKickSession:
    """Stand-in for the session returned by ``_get_kick_session``.

    Mimics ``requests.Session.get()`` by returning :class:`FakeResponse`
    objects and tracks requested URLs for test assertions.
    """

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self.requested_urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.requested_urls.append(url)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeDownloader:
    """Downloader stub exposing only what the Kick helpers use."""

    retry = staticmethod(_perform_retry)

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self.requested_urls: list[str] = []

    def _session_get(self, url: str, **_kwargs: Any) -> Any:
        self.requested_urls.append(url)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeWebSocket:
    """Fake ``websocket-client`` connection."""

    def __init__(
        self,
        recv_results: list[Any] | None = None,
        *,
        send_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self._recv_results = list(recv_results or [])
        self._send_error = send_error
        self._close_error = close_error
        self.sent: list[str] = []
        self.timeout: float | None = None
        self.closed = False

    def send(self, data: str) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(data)

    def recv(self) -> Any:
        result = self._recv_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def close(self) -> None:
        if self._close_error is not None:
            raise self._close_error
        self.closed = True


class FakeTransport:
    """Fake :class:`KickPusherTransport` for live-service orchestration tests."""

    def __init__(self, *, connect_errors: int = 0) -> None:
        self._connect_errors = connect_errors
        self.connected = False
        self.subscribed_to: str | None = None
        self.timeout: float | None = None
        self.close_count = 0

    def connect(self, timeout: float | None) -> None:
        self.timeout = timeout
        if self._connect_errors > 0:
            self._connect_errors -= 1
            msg = "fake connect failure"
            raise ConnectionError(msg)
        self.connected = True

    def subscribe(self, chatroom_id: str) -> None:
        self.subscribed_to = chatroom_id

    def close(self) -> None:
        self.close_count += 1


def make_frame_iterator(batches: list[list[Any]]) -> Any:
    """Return a frame-iterator callable that yields successive ``batches``.

    Each batch is a list of frames; an :class:`Exception` instance within a
    batch is raised at that point (used to drive reconnect logic).
    """
    iterator = iter(batches)

    def frame_iterator(_transport: Any) -> Any:
        for item in next(iterator):
            if isinstance(item, Exception):
                raise item
            yield item

    return frame_iterator


def pusher_frame(event: str, data: Any) -> dict[str, Any]:
    """Build a Pusher frame whose ``data`` is a JSON-encoded string."""
    return {"event": event, "data": json.dumps(data)}

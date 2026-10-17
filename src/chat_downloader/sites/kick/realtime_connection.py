# SPDX-License-Identifier: MIT

"""Anonymous website negotiation and connection-token ownership."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import uuid4

from requests.exceptions import RequestException

from chat_downloader.utils.json_types import get_dict, get_list, get_str

from .api_client import (
    _body_looks_like_challenge,
    _check_status,
    _decode_json,
    _is_safe_bearer_token,
    _raise_for_challenge,
)
from .constants import is_numeric_id
from .errors import KickError, KickServerError
from .http_session import create_kick_session

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

    from .http_session import _KickSession

_BASE = "https://web.kick.com/api/v1/realtime"


@dataclass(frozen=True, slots=True)
class RealtimeConnection:
    """Validated anonymous provider descriptor without a session token."""

    provider: str
    url: str


def parse_connection(payload: JSONDict) -> RealtimeConnection:
    """Accept only the public providers and their credential-free endpoints."""
    data = get_dict(payload, "data")
    if get_str(data, "mode") != "websocket":
        msg = "Kick realtime negotiation did not select WebSockets."
        raise KickError(msg)
    for item in get_list(data, "connections"):
        if not isinstance(item, dict):
            continue
        credentials = get_dict(item, "credentials")
        provider = get_str(item, "provider")
        if provider == "pusher":
            key = get_str(credentials, "app_key")
            cluster = get_str(credentials, "cluster")
            if (
                key.isalnum()
                and cluster.isalnum()
                and key.isascii()
                and cluster.isascii()
                and len(key) <= 128
                and len(cluster) <= 32
            ):
                return RealtimeConnection(
                    provider,
                    f"wss://ws-{cluster}.pusher.com/app/{key}"
                    "?protocol=7&client=js&version=8.4.0&flash=false",
                )
        if provider == "centrifugo":
            url = get_str(credentials, "url")
            if (
                not url.isascii()
                or len(url) > 1024
                or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            ):
                continue
            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            host = parsed.hostname or ""
            if (
                parsed.scheme == "wss"
                and host.startswith("realtime.")
                and host.endswith(".platform.kick.com")
                and parsed.netloc == host
                and parsed.path == "/connection/websocket"
                and not parsed.query
                and not parsed.fragment
            ):
                return RealtimeConnection(provider, url)
    msg = "Kick supplied no supported public realtime connection."
    raise KickError(msg)


class KickRealtimeClient:
    """Own an isolated, login-free HTTP session and anonymous client identity."""

    def __init__(
        self,
        *,
        proxy: dict[str, str] | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
        trust_env: bool = True,
        session: _KickSession | None = None,
    ) -> None:
        """Create a login-free origin-isolated session with a fresh client ID."""
        self._session = session or create_kick_session(
            proxy=proxy,
            extra_headers={"Origin": "https://kick.com", "x-app-platform": "web"},
            trust_env=trust_env,
        )
        self._timeout = timeout
        self.client_id = str(uuid4())
        self._closed = False

    def _post(self, path: str, body: JSONDict) -> JSONDict:
        if self._closed:
            msg = "KickRealtimeClient is closed."
            raise RuntimeError(msg)
        try:
            response = self._session.post(
                _BASE + path, json=body, timeout=self._timeout, allow_redirects=False
            )
        except (RequestException, OSError) as error:
            msg = "Kick anonymous realtime request failed."
            raise ConnectionError(msg) from error
        if response.status_code != 423 and _body_looks_like_challenge(response):
            _raise_for_challenge(response, "realtime")
        _check_status(response, context="realtime", resource="realtime")
        data = _decode_json(response, context="realtime", resource="realtime")
        if not isinstance(data, dict):
            msg = "Kick realtime response was not an object."
            raise KickServerError(msg)
        return data

    def negotiate(self, channel_id: str | None = None) -> RealtimeConnection:
        """Negotiate the global or channel-chat connection independently."""
        if channel_id is not None and not is_numeric_id(channel_id):
            msg = "Invalid Kick realtime channel identity."
            raise ValueError(msg)
        path = (
            f"/channels/{channel_id}/chat/connection" if channel_id else "/connection"
        )
        return parse_connection(
            self._post(
                path,
                {
                    "client": {"id": self.client_id, "type": "web"},
                    "capabilities": {
                        "accepted_providers": [
                            {"provider": "centrifugo"},
                            {"provider": "pusher"},
                        ]
                    },
                },
            )
        )

    def connection_token(self) -> str:
        """Request a short-lived anonymous token; never log or persist it."""
        token = get_str(
            get_dict(
                self._post("/auth/connection", {"client_id": self.client_id}), "data"
            ),
            "token",
        )
        if not _is_safe_bearer_token(token):
            msg = "Kick supplied an invalid anonymous connection token."
            raise KickServerError(msg)
        return token

    def close(self) -> None:
        """Release the owned session exactly once."""
        if not self._closed:
            self._closed = True
            with suppress(OSError, RuntimeError):
                self._session.close()

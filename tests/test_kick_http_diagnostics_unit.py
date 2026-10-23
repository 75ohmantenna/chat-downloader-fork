# SPDX-License-Identifier: MIT

"""Public Kick requests expose timing and status without leaking request data."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from requests.exceptions import RequestException

from chat_downloader.debugging import logger
from chat_downloader.sites.kick import public_state, realtime_connection
from chat_downloader.sites.kick.errors import KickServerError
from tests.kick_helpers import FakeResponse


@pytest.mark.parametrize("endpoint", ["metadata", "viewers", "global", "chat", "token"])
@pytest.mark.parametrize("outcome", [200, 503, "network", "os"])
def test_public_http_status_and_duration_are_logged_without_secrets(
    monkeypatch, caplog, endpoint, outcome
):
    session = MagicMock()
    if endpoint in {"metadata", "viewers"}:
        module = public_state
        client = public_state.KickPublicState(session=session)
        request = session.get
        origin = "public"
        resource = "channel" if endpoint == "metadata" else "viewers"
        payload = {} if endpoint == "metadata" else []

        def invoke():
            if endpoint == "metadata":
                return client.metadata("PRIVATE_CHANNEL")
            return client.viewers({"livestream": {"id": 123}})
    else:
        module = realtime_connection
        client = realtime_connection.KickRealtimeClient(session=session)
        request = session.post
        origin = "web"
        resource = "realtime_auth" if endpoint == "token" else "realtime_connection"
        payload = (
            {"data": {"token": "PRIVATE_TOKEN"}}
            if endpoint == "token"
            else {
                "data": {
                    "mode": "websocket",
                    "connections": [
                        {
                            "provider": "pusher",
                            "credentials": {"app_key": "PRIVATEKEY", "cluster": "us2"},
                        }
                    ],
                }
            }
        )
        invoke = (
            client.connection_token
            if endpoint == "token"
            else lambda: client.negotiate("123" if endpoint == "chat" else None)
        )
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks))
    caplog.set_level("DEBUG", logger=logger.name)
    if outcome in {"network", "os"}:
        request.side_effect = (
            RequestException("PRIVATE_ERROR")
            if outcome == "network"
            else OSError("PRIVATE_ERROR")
        )
        with pytest.raises((RequestException, OSError, ConnectionError)):
            invoke()
    else:
        request.return_value = FakeResponse(outcome, payload)
        if outcome == 503:
            with pytest.raises(KickServerError):
                invoke()
        else:
            invoke()
    status = "transport_error" if isinstance(outcome, str) else str(outcome)
    assert caplog.messages == [
        f"Kick HTTP resource={resource} origin={origin} status={status} elapsed=0.250s"
    ]
    assert "PRIVATE" not in caplog.text
    assert "https://" not in caplog.text
    client.close()

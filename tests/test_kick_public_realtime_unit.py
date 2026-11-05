# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import time
from functools import partial
from queue import Empty, Full, Queue
from threading import Event, Thread, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from curl_cffi.requests import session as curl_session
from requests.exceptions import RequestException

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.kick import live_service
from chat_downloader.sites.kick import public_state as ps
from chat_downloader.sites.kick import public_transport as pt
from chat_downloader.sites.kick import realtime_connection as rc
from chat_downloader.sites.kick.api_client import KickApiClient
from chat_downloader.sites.kick.centrifugo_transport import KickCentrifugoTransport
from chat_downloader.sites.kick.constants import (
    EVENT_NAME_MAP,
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_PING,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.errors import (
    KickCountryBlocked,
    KickError,
    KickServerError,
)
from chat_downloader.sites.kick.live_iterator import KickLiveIterator
from chat_downloader.sites.kick.parsing.events import dispatch_event
from chat_downloader.sites.kick.parsing.public_events import PUBLIC_MESSAGE_TYPES
from chat_downloader.sites.kick.public_state import KickPublicState, category_feeds
from chat_downloader.sites.kick.websocket_transport import KickPusherTransport
from chat_downloader.utils.timed_generator import TimedGenerator
from tests.kick_helpers import (
    FakeDownloader,
    FakeKickSession,
    FakeResponse,
    FakeWebSocket,
    load_fixture,
    pusher_frame,
    raw_message,
)


class PostSession(FakeKickSession):
    post = FakeKickSession.get


def connection(provider="centrifugo", **credentials):
    return {
        "data": {
            "mode": "websocket",
            "connections": [{"provider": provider, "credentials": credentials}],
        }
    }


def http_consumer(endpoint, response):
    session = PostSession([response])
    if endpoint == "api":
        client = KickApiClient(session=session)
        consume = partial(client.fetch_channel, "slug")
    elif endpoint == "realtime":
        client = rc.KickRealtimeClient(session=session)
        consume = client.negotiate
    else:
        client = KickPublicState(session=session)
        consume = (
            partial(client.metadata, "slug")
            if endpoint == "metadata"
            else partial(client.viewers, {"livestream": {"id": 123}})
        )
    return client, session, consume


@pytest.mark.parametrize("endpoint", ["api", "metadata", "viewers", "realtime"])
@pytest.mark.parametrize("status", [300, 302, 399])
def test_kick_http_redirect_never_inspects_body(endpoint, status):
    class RedirectResponse:
        status_code = status

        def __init__(self):
            self.headers = {"Location": "https://foreign.invalid/redirect"}

        @property
        def text(self):
            pytest.fail("Redirect body must not be inspected for challenges.")

        def json(self):
            pytest.fail("Redirect body must not be decoded.")

    client, session, consume = http_consumer(endpoint, RedirectResponse())
    try:
        with pytest.raises(KickServerError, match="Unexpected HTTP redirect"):
            consume()
        assert len(session.calls) == 1
        assert session.calls[0][1]["allow_redirects"] is False
    finally:
        client.close()


@pytest.mark.parametrize("endpoint", ["api", "metadata", "viewers", "realtime"])
@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param(
            FakeResponse(423, text="<html>challenge</html>", malformed=True),
            KickCountryBlocked,
            id="country-before-challenge",
        ),
        pytest.param(
            FakeResponse(200, text="<html>challenge</html>", malformed=True),
            CaptchaChallengeRequired,
            id="successful-status-challenge",
        ),
        pytest.param(
            FakeResponse(503, text="<html>challenge</html>", malformed=True),
            CaptchaChallengeRequired,
            id="challenge-before-server-retry",
        ),
        pytest.param(FakeResponse(403, {}), CaptchaChallengeRequired, id="forbidden"),
        pytest.param(FakeResponse(429, {}), KickServerError, id="rate-limited"),
        pytest.param(FakeResponse(503, {}), KickServerError, id="unavailable"),
        pytest.param(FakeResponse(418, {}), KickError, id="other-terminal-status"),
        pytest.param(FakeResponse(200, malformed=True), KickServerError, id="bad-json"),
    ],
)
def test_kick_http_consumer_status_boundaries(endpoint, response, error):
    client, session, consume = http_consumer(endpoint, response)
    try:
        with pytest.raises(error) as failure:
            consume()
        assert type(failure.value) is error
        assert len(session.calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    ("provider", "credentials", "expected"),
    [
        (
            "centrifugo",
            {"url": "wss://realtime.us-east-1.platform.kick.com/connection/websocket"},
            "realtime.us-east-1",
        ),
        (
            "pusher",
            {"app_key": "newkey", "cluster": "us2"},
            "ws-us2.pusher.com/app/newkey",
        ),
    ],
)
def test_anonymous_negotiation_keeps_origin_and_client_identity(
    provider, credentials, expected
):
    session = PostSession(
        [
            FakeResponse(payload=connection(provider, **credentials)),
            FakeResponse(payload={"data": {"token": "anonymous-token"}}),
        ]
    )
    client = rc.KickRealtimeClient(session=session, timeout=(2, 3))
    assert expected in client.negotiate("123").url
    assert client.connection_token() == "anonymous-token"
    body = session.calls[0][1]["json"]
    assert body["client"]["id"] == session.calls[1][1]["json"]["client_id"]
    assert session.calls[0][0].endswith("/channels/123/chat/connection")
    assert session.calls[0][1]["allow_redirects"] is False
    client.close()
    client.close()
    assert session.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        client.negotiate()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {"mode": "polling"}},
        {"data": {"mode": "websocket", "connections": [None]}},
        connection("unknown"),
        connection("pusher", app_key="x/y", cluster="us2"),
        connection("pusher", app_key="é", cluster="us2"),
        connection("pusher", app_key="a" * 129, cluster="us2"),
        connection(url="wss://[invalid/"),
        connection(
            url="wss://realtime.us-east-1.platform.kick.com/connection/websocket\r\n"
        ),
        *[
            connection(url=url)
            for url in [
                "ws://realtime.us-east-1.platform.kick.com/connection/websocket",
                "wss://evil.test/connection/websocket",
                "wss://user:secret@realtime.us-east-1.platform.kick.com/connection/websocket",
                "wss://realtime.us-east-1.platform.kick.com/connection/websocket?token=secret",
                "wss://realtime.us-east-1.platform.kick.com/other",
            ]
        ],
    ],
)
def test_negotiation_rejects_unsupported_or_credential_bearing_endpoints(payload):
    with pytest.raises(KickError):
        rc.parse_connection(payload)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(423, text="<html>blocked</html>"), KickCountryBlocked),
        (FakeResponse(403, text="<html>challenge</html>"), CaptchaChallengeRequired),
        (FakeResponse(429), KickServerError),
        (FakeResponse(200, payload=[]), KickServerError),
        (FakeResponse(200, malformed=True), KickServerError),
        (RequestException("network secret"), ConnectionError),
    ],
)
def test_negotiation_shares_endpoint_failure_policy(response, error):
    client = rc.KickRealtimeClient(session=PostSession([response]))
    with pytest.raises(error):
        client.negotiate()
    client.close()


def test_anonymous_session_never_inherits_account_headers(monkeypatch):
    factory = MagicMock(
        return_value=PostSession(
            [FakeResponse(payload=connection("pusher", app_key="key", cluster="us2"))]
        )
    )
    monkeypatch.setattr(rc, "create_kick_session", factory)
    client = rc.KickRealtimeClient(
        proxy={"https": "http://proxy.test"}, trust_env=False
    )
    assert client.negotiate().provider == "pusher"
    headers = factory.call_args.kwargs["extra_headers"]
    assert headers == {"Origin": "https://kick.com", "x-app-platform": "web"}
    assert factory.call_args.kwargs["trust_env"] is False
    with pytest.raises(ValueError):
        client.negotiate("../123")
    client.close()


@pytest.mark.parametrize("token", ["", "bad token", "secret\r\nheader", 42])
def test_invalid_anonymous_token_is_not_used(token):
    client = rc.KickRealtimeClient(
        session=PostSession([FakeResponse(payload={"data": {"token": token}})])
    )
    with pytest.raises(KickServerError, match="invalid anonymous"):
        client.connection_token()


def test_compact_gifts_reject_missing_recipient_contract_without_hiding_bad_data():
    event = {
        "event": "GiftedSubscriptionsEvent",
        "data": {"gifter_username": "gifter", "gifted_usernames": [42]},
    }
    assert dispatch_event(event, received_timestamp=1) is None
    event["data"] = {"id": "legacy", "sender": {"username": "gifter"}}
    assert dispatch_event(event, received_timestamp=2)["message_id"] == "legacy"


def test_centrifugo_renewal_transient_failure_reconnects():
    transport, _ws, client = centrifugo()
    transport._refresh_at = 0
    client.connection_token.side_effect = KickServerError("try later")
    with pytest.raises(ConnectionError, match="renewal failed temporarily"):
        transport.recv()


def test_centrifugo_reconnect_clears_pending_commands_frames_and_old_lease():
    transport, first_socket, _client = centrifugo()
    transport.subscribe_channels(["old-feed"])
    transport._frames.append({"event": "stale"})
    transport._refresh_at = 0
    transport._pong_required = True
    second_socket = FakeWebSocket([json.dumps({"id": 1, "connect": {}})])
    transport._connector = lambda *_a, **_kw: second_socket
    transport.connect(1)
    assert first_socket.closed
    assert json.loads(second_socket.sent[0])["id"] == 1
    assert set(transport._commands) == {1}
    assert transport._refresh_at == float("inf")
    assert not transport._pong_required
    assert transport.recv()["event"] == PUSHER_CONNECTION_ESTABLISHED


def test_viewer_poll_failure_preserves_successful_metadata_and_chat():
    transport = public_state_transport()
    transport._state.viewers.side_effect = KickServerError("viewer endpoint offline")
    counters = []
    transport._diagnostic_callback = counters.append
    transport._poll()
    transport._queue.put({"event": "chat"})
    assert transport.recv()["event"] == "kick:public_state"
    assert transport.recv()["event"] == "chat"
    assert counters == ["public_state_poll_count", "public_state_poll_failure_count"]


def test_public_state_challenge_keeps_access_policy():
    state = KickPublicState(
        session=FakeKickSession([FakeResponse(200, text="<html>challenge</html>")])
    )
    with pytest.raises(CaptchaChallengeRequired):
        state.metadata("slug")


@pytest.mark.parametrize("redirect_at", ["metadata", "viewers"])
def test_public_http_redirect_poll_preserves_other_snapshot_and_chat(redirect_at):
    channel = {"livestream": {"id": 123}}
    viewers = [{"livestream_id": 123, "viewers": 42}]
    redirect = FakeResponse(302, text="<html>redirect, not a challenge</html>")
    session = FakeKickSession(
        [
            redirect if redirect_at == "metadata" else FakeResponse(200, channel),
            redirect if redirect_at == "viewers" else FakeResponse(200, viewers),
        ]
    )
    transport = public_state_transport()
    transport._state = KickPublicState(session=session)
    transport._channel = channel
    transport._state._channel = channel
    counters = []
    transport._diagnostic_callback = counters.append
    transport._poll()
    transport._queue.put({"event": "chat"})
    try:
        snapshot = transport.recv()
        if redirect_at == "metadata":
            assert snapshot["event"] == "kick:viewer_count"
            assert snapshot["data"] == viewers
        else:
            assert snapshot["event"] == "kick:public_state"
            assert snapshot["data"]["livestream"] == channel["livestream"]
        assert transport.recv() == {"event": "chat"}
        assert counters.count("public_state_poll_failure_count") == 1
        assert not transport._reconnect_requested.is_set()
        assert len(session.calls) == 2
    finally:
        transport.close()
        transport._state.close()
    assert session.close_calls == 1


@pytest.mark.parametrize("owner", [KickPublicState, rc.KickRealtimeClient])
def test_anonymous_http_cleanup_is_idempotent_and_cannot_mask_failure(owner):
    session = MagicMock()
    session.close.side_effect = OSError("cleanup failed")
    client = owner(session=session)
    client.close()
    client.close()
    session.close.assert_called_once()
    if owner is KickPublicState:
        with pytest.raises(RuntimeError, match="closed"):
            client.metadata("slug")


def centrifugo(frames=()):
    ws = FakeWebSocket(list(frames))
    client = SimpleNamespace(connection_token=MagicMock(return_value="secret-token"))
    transport = KickCentrifugoTransport(
        client=client,
        url="wss://realtime.us-east-1.platform.kick.com/connection/websocket",
        connector=lambda *_a, **_kw: ws,
    )
    transport.connect(1)
    return transport, ws, client


def test_centrifugo_batches_subscriptions_publications_and_keepalive():
    fixture = load_fixture("public_realtime_contracts.json")
    data = {
        "event": "App\\Events\\ChatMessageEvent",
        "data": {"id": "message", "content": "hello"},
    }
    frames = [
        json.dumps(fixture["connect_reply"])
        + "\n"
        + json.dumps({"id": 2, "subscribe": {}}),
        json.dumps({"push": {"channel": "chat", "pub": {"data": data}}}),
        "{}",
    ]
    transport, ws, _ = centrifugo(frames)
    transport.subscribe_channels(["chat"])
    assert transport.recv()["event"] == PUSHER_CONNECTION_ESTABLISHED
    assert transport.recv() == {
        "event": PUSHER_SUBSCRIPTION_SUCCEEDED,
        "channel": "chat",
        "data": {},
    }
    assert transport.recv() == {**data, "channel": "chat"}
    assert transport.recv()["event"] == PUSHER_PING
    transport.send_pong()
    assert json.loads(ws.sent[-1]) == {}
    assert len(json.loads(ws.sent[0])["connect"]["name"]) <= 16
    transport.close()


def test_centrifugo_refreshes_anonymous_lease_without_login(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(
        "chat_downloader.sites.kick.centrifugo_transport.time.monotonic", lambda: now[0]
    )
    transport, ws, client = centrifugo(
        [
            json.dumps({"id": 1, "connect": {"expires": True, "ttl": 10}}),
            TimeoutError(),
            json.dumps({"id": 2, "refresh": {"expires": True, "ttl": 20}}),
        ]
    )
    assert transport.recv()["event"] == PUSHER_CONNECTION_ESTABLISHED
    transport.send_pong()
    assert len(ws.sent) == 1
    now[0] = 109.0
    assert transport.recv() is None
    assert "refresh" in json.loads(ws.sent[-1])
    assert client.connection_token.call_count == 2
    assert transport.recv() is None
    assert transport._refresh_at > now[0]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"id": 99, "connect": {}}, ConnectionError),
        ({"id": 1}, ConnectionError),
        ({"id": 1, "error": {"temporary": True}}, ConnectionError),
        ({"id": 1, "error": {"code": 101}}, KickError),
        ({"id": 1, "connect": {"expires": True, "ttl": 0}}, ConnectionError),
        ({"push": {"disconnect": {"code": 3500}}}, KickError),
        ({"push": {"unsubscribe": {"code": 4500}}}, KickError),
        ({"push": {"disconnect": {"code": 3000}}}, ConnectionError),
    ],
)
def test_centrifugo_surfaces_rejected_commands_and_disconnects(reply, error):
    transport, _ws, _client = centrifugo([json.dumps(reply)])
    with pytest.raises(error):
        transport.recv()


@pytest.mark.parametrize(
    ("raw", "count"),
    [
        ("bad JSON", 1),
        ("[]", 1),
        ('{"push":{}}', 1),
        ('{"push":{"pub":{"data":[]}}}', 1),
        ('{"push":{"pub":{"data":"bad json"}}}', 1),
        (
            '{"push":{"channel":"chat","pub":{"data":"{\\"event\\":\\"Example\\",\\"data\\":{}}"}}}',
            0,
        ),
    ],
)
def test_centrifugo_malformed_wire_frames_are_counted_without_sampling(raw, count):
    transport, _ws, _client = centrifugo([raw])
    diagnostics = []
    transport._diagnostic_callback = diagnostics.append
    frame = transport.recv()
    assert diagnostics == ["invalid_websocket_frame_count"] * count
    assert (frame is not None) == (count == 0)


@pytest.mark.parametrize("deadline", ["heartbeat", "ack"])
def test_centrifugo_times_out_stalled_protocol(deadline):
    transport, _ws, _client = centrifugo()
    if deadline == "heartbeat":
        transport._heartbeat_at = 0
    else:
        transport._commands[1] = ("connect", "", 0)
    with pytest.raises(ConnectionError, match="timed out"):
        transport.recv()


@pytest.mark.parametrize("message_type", sorted(PUBLIC_MESSAGE_TYPES))
def test_every_public_event_preserves_evolving_payload_and_source(message_type):
    event = next(name for name, kind in EVENT_NAME_MAP.items() if kind == message_type)
    payload = {"id": "entity", "future": {"nested": [1, None, "value"]}}
    first = dispatch_event(
        {"event": event, "channel": "feed", "data": json.dumps(payload)},
        received_timestamp=100,
    )
    second = dispatch_event(
        {"event": event, "channel": "feed", "data": payload}, received_timestamp=101
    )
    assert first["message_type"] == message_type
    assert first["metadata"]["data"] == payload
    assert first["metadata"]["channel"] == "feed"
    assert first["metadata"]["source"] == "websocket"
    assert first["message_id"] != second["message_id"]


def test_current_clear_host_and_chunked_gifts_contracts():
    fixture = load_fixture("public_realtime_contracts.json")
    clear, host, gifts = [
        dispatch_event(frame, received_timestamp=100 + index)
        for index, frame in enumerate(fixture["events"][:3])
    ]
    assert clear["message_type"] == "chat_clear"
    assert host["author"]["name"] == "host"
    assert host["metadata"]["number_viewers"] == 42
    assert gifts["metadata"]["quantity"] == 1
    assert gifts["metadata"]["kick_event"]["data"]["gifted_total"] == 50
    next_chunk = fixture["events"][2]
    next_chunk["data"]["chunk_details"]["chunk_index"] = 1
    second = dispatch_event(next_chunk, received_timestamp=110)
    assert gifts["message_id"] != second["message_id"]
    assert (
        second["message_id"]
        == dispatch_event(next_chunk, received_timestamp=111)["message_id"]
    )


def test_public_signal_null_and_missing_time_or_payload():
    event = "App\\Events\\StopStreamBroadcast"
    assert dispatch_event({"event": event, "data": None}, received_timestamp=1)
    assert dispatch_event({"event": event, "data": {}}, received_timestamp=True) is None
    assert dispatch_event({"event": event}, received_timestamp=1) is None


def test_public_state_validates_shapes_and_uses_only_current_livestream():
    channel = {
        "livestream": {
            "id": 123,
            "categories": [{"id": 2}, {"id": 2}, {"id": True}, None],
        }
    }
    assert category_feeds(channel) == ["drops_category_2"]
    session = FakeKickSession(
        [
            FakeResponse(payload=channel),
            FakeResponse(payload=[{"livestream_id": 123, "viewers": 42}]),
        ]
    )
    state = KickPublicState(session=session)
    assert state.metadata("slug") == channel
    assert state.viewers(channel)[0]["viewers"] == 42
    assert session.calls[1][1]["params"] == {"ids[]": "123"}
    assert state.viewers({}) == []
    state.close()
    assert session.close_calls == 1


@pytest.mark.parametrize(("method", "payload"), [("metadata", []), ("viewers", {})])
def test_public_state_rejects_wrong_json_shapes(method, payload):
    state = KickPublicState(session=FakeKickSession([FakeResponse(payload=payload)]))
    with pytest.raises(TypeError):
        getattr(state, method)(
            "slug" if method == "metadata" else {"livestream": {"id": 1}}
        )


def test_deadline_interrupts_blocked_live_receive_without_late_reconnect():
    entered, released = Event(), Event()
    source = KickLiveIterator()
    transport = SimpleNamespace(request_stop=released.set)
    source.transport = transport

    def blocked():
        entered.set()
        assert released.wait(2)
        if not source.stopped.is_set():
            yield {"message_id": "unexpected"}

    source.source = blocked()
    iterator = TimedGenerator(source, timeout=0.1)
    assert list(iterator) == []
    assert entered.is_set()
    iterator.close()
    assert iterator.deadline_prefetch_summary() == (0, True)
    assert list(source) == []


def test_pusher_requires_each_subscription_acknowledgement():
    transport = pt._NegotiatedPusherTransport()
    ws = FakeWebSocket(
        [
            json.dumps({"event": PUSHER_SUBSCRIPTION_SUCCEEDED, "channel": "one"}),
            TimeoutError(),
        ]
    )
    transport._ws = ws
    transport.subscribe_channels(["one", "two"])
    assert transport.recv()["channel"] == "one"
    assert set(transport._pending_subscriptions) == {"two"}
    assert transport.recv() is None
    transport._pending_subscriptions["two"] = 0
    with pytest.raises(ConnectionError, match="acknowledgement"):
        transport.recv()


class BlockingSocket(FakeWebSocket):
    def __init__(self, frames):
        super().__init__(frames)
        self.aborted = Event()

    def recv(self):
        if self._recv_results:
            return super().recv()
        if self.aborted.wait(0.01):
            raise OSError("closed")
        raise TimeoutError

    def abort(self):
        self.aborted.set()

    def close(self):
        self.abort()
        super().close()


@pytest.mark.parametrize("provider", ["pusher", "centrifugo"])
def test_public_transport_owns_independent_connections_and_all_public_feeds(
    monkeypatch, provider
):
    channel = {"livestream": {"id": 123, "categories": [{"id": 4}]}}
    setup_state = KickPublicState(session=MagicMock())
    setup_state.metadata = MagicMock(return_value=channel)
    state = KickPublicState(session=MagicMock())
    state.metadata = MagicMock(return_value=channel)
    state.viewers = MagicMock(return_value=[{"livestream_id": 123, "viewers": 42}])
    state.close = MagicMock(wraps=state.close)
    monkeypatch.setattr(
        pt, "KickPublicState", MagicMock(side_effect=[setup_state, state])
    )
    clients = [MagicMock(), MagicMock()]
    for index, client in enumerate(clients):
        client.negotiate.return_value = rc.RealtimeConnection(
            provider, f"wss://provider-{index}.test/"
        )
        client.connection_token.return_value = "secret-token"
    client_factory = MagicMock(side_effect=clients)
    monkeypatch.setattr(pt, "KickRealtimeClient", client_factory)
    global_feeds = [
        "channel.123",
        "channel_123",
        "predictions-channel-123",
        "chatrooms.456",
        "chatroom_456",
        "drops_category_4",
    ]
    sockets = []
    for feeds in (["chatrooms.456.v2"], global_feeds):
        if provider == "pusher":
            frames = [
                {"event": PUSHER_CONNECTION_ESTABLISHED},
                *[
                    {"event": PUSHER_SUBSCRIPTION_SUCCEEDED, "channel": feed}
                    for feed in feeds
                ],
            ]
        else:
            frames = [
                {"id": 1, "connect": {}},
                *[
                    {"id": index + 2, "subscribe": {}}
                    for index, _feed in enumerate(feeds)
                ],
            ]
        sockets.append(BlockingSocket([json.dumps(frame) for frame in frames]))
    connector = MagicMock(side_effect=sockets)
    monkeypatch.setattr(pt, "_default_connector", connector)
    counters = []
    transport = pt.KickPublicTransport(
        username="slug",
        channel_id="123",
        diagnostic_callback=counters.append,
        trust_env=False,
        proxy={"https": "http://proxy.test"},
    )
    transport.connect(1, force_discover=True)
    transport.subscribe("456")
    try:
        transport.set_timeout(99)
        transport.set_timeout(99)
        assert len(transport._workers) == 2
        frames = []
        end = time.monotonic() + 2
        while (
            len([f for f in frames if f.get("event") == PUSHER_SUBSCRIPTION_SUCCEEDED])
            < 7
            or not {"kick:public_state", "kick:viewer_count"}
            <= {f["event"] for f in frames}
        ) and time.monotonic() < end:
            frame = transport.recv()
            if frame is not None:
                frames.append(frame)
        transport.send_pong()
    finally:
        transport.close()
    transport.close()
    assert all(not worker.is_alive() for worker in transport._workers)
    assert not transport._poll_worker.is_alive()
    setup_state.metadata.assert_called_once_with("slug")
    setup_state._session.close.assert_called_once()
    state.metadata.assert_not_called()
    assert {
        f["channel"] for f in frames if f.get("event") == PUSHER_SUBSCRIPTION_SUCCEEDED
    } == {"chatrooms.456.v2", *global_feeds}
    assert {f["event"] for f in frames} >= {"kick:public_state", "kick:viewer_count"}
    assert counters.count("public_subscription_count") == 7
    assert counters.count(f"{provider}_connection_count") == 2
    assert clients[0].negotiate.call_args.args == ("123",)
    assert clients[1].negotiate.call_args.args == (None,)
    assert connector.call_args.kwargs["origin"] == "https://kick.com"
    assert connector.call_args.kwargs["proxy_url"] == "http://proxy.test"
    assert all(socket.closed for socket in sockets)
    assert state.close.call_count == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KickError("unsupported"), KickError),
        (KickServerError("transient"), ConnectionError),
        (RequestException("offline"), ConnectionError),
    ],
)
def test_public_setup_failure_releases_partial_sessions(monkeypatch, error, expected):
    state = MagicMock()
    state.metadata.return_value = {}
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
    client = MagicMock()
    client.negotiate.side_effect = error
    monkeypatch.setattr(pt, "KickRealtimeClient", MagicMock(return_value=client))
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    with pytest.raises(expected):
        transport.connect(None)
    client.close.assert_called_once()
    state.close.assert_called_once()


@pytest.mark.parametrize("redirect_at", ["metadata", "chat", "global"])
def test_live_opener_retries_public_http_redirect_then_subscribes(
    monkeypatch, redirect_at
):
    redirect = FakeResponse(
        302, text="<html>redirect is not a challenge</html>", malformed=True
    )
    redirect.headers["Location"] = "https://foreign.invalid/redirect"
    descriptor = connection("pusher", app_key="publickey", cluster="us2")
    state_sessions = [
        FakeKickSession(
            [redirect if redirect_at == "metadata" else FakeResponse(200, {})]
        ),
        FakeKickSession([FakeResponse(200, {})]),
    ]
    state_sessions.append(FakeKickSession([]))
    snapshot_created = Event()
    pending_states = iter(state_sessions)

    def create_state_session(**_kwargs):
        session = next(pending_states)
        if session is state_sessions[-1]:
            snapshot_created.set()
        return session

    realtime_sessions = [
        *(
            []
            if redirect_at == "metadata"
            else [
                PostSession(
                    [
                        redirect
                        if redirect_at == "chat"
                        else FakeResponse(200, descriptor)
                    ]
                )
            ]
        ),
        *([PostSession([redirect])] if redirect_at == "global" else []),
        PostSession([FakeResponse(200, descriptor)]),
        PostSession([FakeResponse(200, descriptor)]),
    ]
    state_factory = MagicMock(side_effect=create_state_session)
    realtime_factory = MagicMock(side_effect=realtime_sessions)
    monkeypatch.setattr(ps, "create_kick_session", state_factory)
    monkeypatch.setattr(rc, "create_kick_session", realtime_factory)
    sockets = [
        BlockingSocket([json.dumps({"event": PUSHER_CONNECTION_ESTABLISHED})])
        for _ in range(3 if redirect_at == "global" else 2)
    ]
    connector = MagicMock(side_effect=sockets)
    monkeypatch.setattr(pt, "_default_connector", connector)
    attempts = []

    def factory():
        transport = pt.KickPublicTransport(
            username="slug", channel_id="123", trust_env=False
        )
        attempts.append(transport)
        return transport

    opened = None
    try:
        opened = live_service._open_subscribed_transport(
            FakeDownloader(),
            "456",
            ChatRequest(max_attempts=2, retry_timeout=0, interruptible_retry=False),
            factory,
        )
        assert len(attempts) == 2
        assert opened is attempts[1]
        assert snapshot_created.wait(1)
        assert attempts[0]._stopped.is_set()
        assert state_sessions[0].close_calls == 1
        assert all(session.close_calls == 1 for session in realtime_sessions[:-2])
        assert {
            json.loads(message)["data"]["channel"]
            for socket in sockets[-2:]
            for message in socket.sent
        } == {
            "chatrooms.456.v2",
            "channel.123",
            "channel_123",
            "predictions-channel-123",
            "chatrooms.456",
            "chatroom_456",
        }
        assert all(
            call[1]["allow_redirects"] is False
            for session in [*state_sessions, *realtime_sessions]
            for call in session.calls
        )
    finally:
        if opened is not None:
            opened.close()
    assert all(
        session.close_calls == 1 for session in [*state_sessions, *realtime_sessions]
    )
    assert all(socket.closed for socket in sockets)


def public_state_transport():
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    transport._receive_timeout = 0.001
    transport._state = KickPublicState(session=MagicMock())
    transport._state.metadata = MagicMock(return_value={})
    transport._state.viewers = MagicMock(return_value=[])
    return transport


@pytest.mark.timeout(5)
@pytest.mark.parametrize("blocked_resource", ["metadata", "viewers"])
@pytest.mark.parametrize("stop_reason", ["close", "reconnect"])
def test_public_poll_cannot_delay_queued_chat_or_close_its_inflight_session(
    monkeypatch, blocked_resource, stop_reason
):
    entered, release = Event(), Event()
    channel = {"livestream": {"id": 123}}

    class BlockedSession(FakeKickSession):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            metadata_request = "/api/v2/channels/" in url
            if metadata_request == (blocked_resource == "metadata"):
                entered.set()
                assert release.wait(5)
                assert self.close_calls == 0
            return FakeResponse(
                payload=channel if metadata_request else [{"viewers": 42}]
            )

    session = BlockedSession([])
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    monkeypatch.setattr(ps, "create_kick_session", lambda **_kwargs: session)
    transport._channel = channel
    monkeypatch.setattr(pt, "_PUBLIC_STATE_POLL_INTERVAL", 0)
    try:
        transport.set_timeout(0.001)
        assert entered.wait(1)
        assert transport._workers == []
        assert transport._poll_worker.is_alive()
        transport._put({"event": "chat", "data": {"id": "queued"}})
        for _ in range(3):
            frame = transport.recv()
            if frame["event"] == "chat":
                break
        else:
            pytest.fail("Queued chat waited for the blocked REST request.")
        assert frame["data"] == {"id": "queued"}
        assert not release.is_set()
        if stop_reason == "reconnect":
            transport._request_reconnect(ConnectionError("socket failed"))
            with pytest.raises(ConnectionError, match="socket failed"):
                transport.recv()
            assert not transport._stopped.is_set()
        transport.close()
        assert session.close_calls == 0
        assert transport._poll_worker.is_alive()
        assert transport._poll_stopped.is_set()
    finally:
        transport.close()
        release.set()
        if transport._poll_worker is not None:
            transport._poll_worker.join(1)
    assert not transport._poll_worker.is_alive()
    assert session.close_calls == 1
    assert transport._pending.empty()


@pytest.mark.timeout(5)
def test_slow_public_consumer_backpressures_at_two_pending_snapshots(monkeypatch):
    transport = public_state_transport()
    state = transport._state
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
    state.metadata.return_value = {"followers_count": 1}
    backpressured = Event()

    class ObservedQueue(Queue):
        def put(self, item, block=True, timeout=None):
            try:
                return super().put(item, block=block, timeout=timeout)
            except Full:
                backpressured.set()
                raise

    transport._pending = ObservedQueue(maxsize=2)
    monkeypatch.setattr(pt, "_PUBLIC_STATE_POLL_INTERVAL", 0)
    try:
        transport.set_timeout(0.001)
        assert backpressured.wait(1)
        worker = transport._poll_worker
        transport.set_timeout(0.001)
        assert transport._poll_worker is worker
        assert transport._pending.qsize() == transport._pending.maxsize == 2
        state.metadata.assert_called_once_with("slug")
        state.viewers.assert_called_once_with({})
        transport.close()
        transport._poll_worker.join(1)
        assert not transport._poll_worker.is_alive()
        assert transport._pending.qsize() == 2
    finally:
        transport.close()
    state._session.close.assert_called_once()


def test_poll_worker_transfers_access_error_after_queued_snapshots(monkeypatch):
    session = FakeKickSession([FakeResponse(200, text="<html>challenge</html>")])
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    monkeypatch.setattr(ps, "create_kick_session", lambda **_kwargs: session)
    monkeypatch.setattr(pt, "_PUBLIC_STATE_POLL_INTERVAL", 0)
    try:
        transport.set_timeout(0.001)
        transport._poll_worker.join(1)
        assert not transport._poll_worker.is_alive()
        assert transport.recv()["event"] == "kick:public_state"
        assert transport.recv()["event"] == "kick:viewer_count"
        with pytest.raises(CaptchaChallengeRequired):
            transport.recv()
    finally:
        transport.close()
    assert session.close_calls == 1


def test_category_poll_stops_worker_and_drains_snapshots_and_accepted_chat(monkeypatch):
    transport = public_state_transport()
    state = transport._state
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
    transport._channel = {"livestream": {"categories": [{"id": 1}]}}
    polled = Event()

    def metadata(_username):
        polled.set()
        return {"livestream": {"categories": [{"id": 2}]}}

    state.metadata.side_effect = metadata
    transport._put({"event": "accepted chat"})
    monkeypatch.setattr(pt, "_PUBLIC_STATE_POLL_INTERVAL", 0)
    try:
        transport.set_timeout(0.001)
        assert polled.wait(1)
        assert transport.recv()["data"]["livestream"]["categories"] == [{"id": 1}]
        assert transport.recv()["event"] == "kick:viewer_count"
        transport._poll_worker.join(1)
        assert not transport._poll_worker.is_alive()
        assert transport.recv()["data"]["livestream"]["categories"] == [{"id": 2}]
        assert transport.recv()["event"] == "accepted chat"
        with pytest.raises(ConnectionError, match="category feeds changed"):
            transport.recv()
        assert transport._poll_stopped.is_set()
        assert not transport._stopped.is_set()
        state._session.close.assert_called_once()
    finally:
        transport.close()


def test_cancellation_during_poll_failure_keeps_stop_as_terminal_outcome():
    transport = public_state_transport()

    def fail(*_args, **_kwargs):
        transport.request_stop()
        msg = "request completed after cancellation"
        raise RuntimeError(msg)

    transport._state.snapshots = fail
    try:
        transport._poll()
        assert transport._reader_errors.empty()
        with pytest.raises(ConnectionError, match="stopped"):
            transport.recv()
    finally:
        transport.close()


@pytest.mark.timeout(5)
@pytest.mark.parametrize("cancel", [False, True])
def test_poll_session_construction_failure_transfers_error_unless_cancelled(
    monkeypatch, cancel
):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    constructing, release = Event(), Event()

    def create(**_kwargs):
        constructing.set()
        assert release.wait(5)
        if cancel:
            transport.request_stop()
        msg = "session initialization failed"
        raise RequestException(msg)

    monkeypatch.setattr(pt, "KickPublicState", create)
    try:
        transport.set_timeout(0.001)
        assert constructing.wait(1)
        release.set()
        transport._poll_worker.join(1)
        assert not transport._poll_worker.is_alive()
        if cancel:
            assert transport._reader_errors.empty()
            with pytest.raises(ConnectionError, match="stopped"):
                transport.recv()
        else:
            with pytest.raises(RequestException, match="session initialization failed"):
                transport.recv()
    finally:
        release.set()
        transport.close()


@pytest.mark.timeout(5)
def test_curl_snapshot_handles_close_on_each_creating_thread_before_thread_exit(
    monkeypatch,
):
    handles = []
    ready, viewers_read, release = Event(), Event(), Event()
    channel = {"livestream": None}

    class NativeHandle:
        def __init__(self, *, debug):
            self.owner = get_ident()
            self.closed_by = []
            handles.append(self)

        def close(self):
            self.closed_by.append(get_ident())

    monkeypatch.setattr(curl_session, "Curl", NativeHandle)

    def metadata(state, _username):
        assert state._session.curl.owner == get_ident()
        return channel

    def viewers(state, _channel):
        assert state._session.curl.owner == get_ident()
        viewers_read.set()
        return []

    monkeypatch.setattr(KickPublicState, "metadata", metadata)
    monkeypatch.setattr(KickPublicState, "viewers", viewers)
    clients = [MagicMock(), MagicMock()]
    for client in clients:
        client.negotiate.return_value = rc.RealtimeConnection(
            "pusher", "wss://ws-us2.pusher.com/app/key"
        )
    monkeypatch.setattr(pt, "KickRealtimeClient", MagicMock(side_effect=clients))
    sockets = [
        BlockingSocket([json.dumps({"event": PUSHER_CONNECTION_ESTABLISHED})])
        for _ in range(2)
    ]
    monkeypatch.setattr(pt, "_default_connector", MagicMock(side_effect=sockets))
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    errors = []

    def capture():
        try:
            transport.connect(1)
            transport.subscribe("456")
            transport.set_timeout(0.001)
            ready.set()
            assert release.wait(5)
        except BaseException as error:
            errors.append(error)
            ready.set()

    owner = Thread(target=capture)
    try:
        owner.start()
        assert ready.wait(1)
        assert viewers_read.wait(1)
        assert not errors
        transport.close()
        transport.close()
        assert owner.is_alive()
        assert len(handles) == 2
        assert len({handle.owner for handle in handles}) == 2
        assert all(handle.closed_by == [handle.owner] for handle in handles)
    finally:
        transport.close()
        release.set()
        owner.join(1)
    assert not owner.is_alive()
    assert not errors


def test_cancelled_poll_worker_never_constructs_snapshot_session(monkeypatch):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    factory = MagicMock()
    monkeypatch.setattr(pt, "KickPublicState", factory)
    transport.request_stop()
    transport._poll_loop()
    transport.close()
    transport.close()
    factory.assert_not_called()
    assert transport._pending.empty()


def test_poll_worker_start_failure_never_constructs_snapshot_session(monkeypatch):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    factory = MagicMock()
    monkeypatch.setattr(pt, "KickPublicState", factory)
    worker = MagicMock()
    worker.start.side_effect = RuntimeError("unable to start poller")
    monkeypatch.setattr(pt, "Thread", MagicMock(return_value=worker))
    try:
        with pytest.raises(RuntimeError, match="unable to start poller"):
            transport.set_timeout(0.001)
        assert transport._poll_worker is None
    finally:
        transport.close()
    worker.join.assert_not_called()
    transport.close()
    factory.assert_not_called()


@pytest.mark.timeout(5)
def test_public_close_does_not_wait_for_inflight_setup_metadata(monkeypatch):
    entered, release, closed = Event(), Event(), Event()

    class BlockedSession(FakeKickSession):
        def get(self, url, **kwargs):
            entered.set()
            assert release.wait(5)
            assert self.close_calls == 0
            return FakeResponse(payload={"livestream": None})

    session = BlockedSession([])
    monkeypatch.setattr(ps, "create_kick_session", lambda **_kwargs: session)
    clients = MagicMock()
    monkeypatch.setattr(pt, "KickRealtimeClient", clients)
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    errors = []

    def connect():
        try:
            transport.connect(1)
        except ConnectionError as error:
            errors.append(error)

    def close():
        transport.request_stop()
        transport.close()
        transport.close()
        closed.set()

    request = Thread(target=connect)
    cleanup = Thread(target=close)
    try:
        request.start()
        assert entered.wait(1)
        cleanup.start()
        assert closed.wait(1)
        assert request.is_alive()
        assert session.close_calls == 0
        assert transport._poll_worker is None
    finally:
        release.set()
        request.join(1)
        if cleanup.ident is not None:
            cleanup.join(1)
        transport.close()
    assert not request.is_alive()
    assert not cleanup.is_alive()
    assert len(errors) == 1
    clients.assert_not_called()
    assert session.close_calls == 1


def test_public_snapshots_only_emit_changed_values_and_restart_changed_categories():
    transport = public_state_transport()
    transport._poll()
    assert transport.recv()["event"] == "kick:public_state"
    assert transport.recv()["event"] == "kick:viewer_count"
    transport._poll()
    transport._state.metadata.assert_called_once_with("slug")
    assert transport.recv() is None
    transport._next_poll = 0
    transport._poll()
    assert transport.recv() is None
    transport._state.viewers.return_value = [{"viewers": 42}]
    transport._next_poll = 0
    transport._poll()
    assert transport.recv()["event"] == "kick:viewer_count"
    transport._state.metadata.return_value = {"livestream": {"categories": [{"id": 7}]}}
    transport._next_poll = 0
    transport._poll()
    assert transport.recv()["event"] == "kick:public_state"
    with pytest.raises(ConnectionError, match="category feeds changed"):
        transport.recv()
    transport._queue.put({"event": "publication"})
    assert transport.recv()["event"] == "publication"
    transport._queue.put(KickError("rejected"))
    with pytest.raises(KickError, match="rejected"):
        transport.recv()
    transport.close()
    with pytest.raises(ConnectionError, match="stopped"):
        transport.recv()


def test_public_viewer_snapshots_track_offline_transitions_with_empty_counts():
    from chat_downloader.formatting.format import ItemFormatter

    transport = public_state_transport()
    for channel, expected in [
        ({"livestream": {"id": 123}}, "viewer count"),
        ({"livestream": None}, "viewer count: offline"),
        ({"livestream": {"id": 123}}, "viewer count"),
    ]:
        transport._state.metadata.return_value = channel
        transport._next_poll = 0
        transport._poll()
        assert transport.recv()["event"] == "kick:public_state"
        frame = transport.recv()
        assert frame["event"] == "kick:viewer_count"
        item = dispatch_event(frame, received_timestamp=1_577_836_800_000_000)
        assert ItemFormatter().format(item, "kick") == (
            "2020-01-01 00:00:00 [received] | [channel metadata] "
            f"{expected.capitalize()}"
        )
        transport._next_poll = 0
        transport._poll()
        assert transport.recv() is None
    transport.close()


def test_public_poll_failure_is_visible_and_does_not_drop_chat():
    transport = public_state_transport()
    transport._state.metadata.side_effect = RequestException("offline")
    counters = []
    transport._diagnostic_callback = counters.append
    transport._poll()
    transport._queue.put({"event": "chat"})
    assert transport.recv()["event"] == "kick:viewer_count"
    assert transport.recv()["event"] == "chat"
    assert counters == ["public_state_poll_failure_count"]
    transport._state = None
    transport._poll()


def test_bounded_queue_shutdown_interrupts_backpressure_and_socket_errors(monkeypatch):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    for _ in range(1024):
        transport._queue.put({"event": "buffered"})
    entered = Event()

    def write():
        entered.set()
        transport._put({"event": "extra"})

    worker = Thread(target=write)
    worker.start()
    assert entered.wait(1)
    transport.request_stop()
    worker.join(1)
    assert not worker.is_alive()
    transport._stopped.clear()
    transport._queue.get()
    monkeypatch.setattr(
        pt, "read_frames", MagicMock(side_effect=ConnectionError("lost"))
    )
    child = MagicMock()
    transport._read(child)
    assert child.close.call_count == 1
    transport._stopped.set()
    monkeypatch.setattr(pt, "read_frames", lambda _transport: iter([{}]))
    transport._read(child)
    assert child.close.call_count == 2


def test_worker_access_failure_reaches_consumer_and_closes_its_http_owner(monkeypatch):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    transport._receive_timeout = 0.001
    monkeypatch.setattr(
        pt, "read_frames", MagicMock(side_effect=CaptchaChallengeRequired("blocked"))
    )
    child, client = MagicMock(), MagicMock()
    transport._read(child, client)
    with pytest.raises(CaptchaChallengeRequired, match="blocked"):
        transport.recv()
    child.close.assert_called_once()
    client.close.assert_called_once()


def test_category_reconnect_drains_recorded_prediction_before_resuming_live_chat():
    first = public_state_transport()
    first._channel = {"livestream": {"categories": [{"id": 1}]}}
    first._state.metadata.return_value = {"livestream": {"categories": [{"id": 2}]}}
    prediction = load_fixture("predictions_recorded.json")["events"][0]["frame"]
    first._queue.put(prediction)
    first.connect = lambda *_args, **_kwargs: None
    first.subscribe = lambda *_args: None
    first.set_timeout = lambda *_args: None
    first._poll()
    replacement = MagicMock()
    replacement.recv.side_effect = [
        {"event": PUSHER_SUBSCRIPTION_SUCCEEDED, "channel": "chatrooms.456.v2"},
        pusher_frame(
            "App\\Events\\SubscriptionEvent", load_fixture("subscription_event.json")
        ),
    ]
    transports = iter([first, replacement])
    client = MagicMock()
    client.fetch_preloaded_chat_state.return_value = None
    client.fetch_message_page.return_value = {"data": {"messages": [], "cursor": None}}
    source = live_service._iter_chat_messages(
        SimpleNamespace(_kick_client=client, _http_timeout=(1, 1)),
        "slug",
        "123",
        "456",
        ChatRequest(message_groups=["all"], retry_timeout=0, interruptible_retry=False),
        live_service._KickLiveDiagnostics(),
        transport_factory=lambda: next(transports),
    )
    received = []
    try:
        for item in source:
            received.append(item)
            if item["message_type"] == "subscription":
                break
    finally:
        source.close()
    assert [item["message_type"] for item in received] == [
        "channel_metadata",
        "viewer_count",
        "prediction_updated",
        "subscription",
    ]
    assert received[2]["metadata"]["data"] == prediction["data"]
    client.fetch_message_page.assert_called_once()
    replacement.close.assert_called()


@pytest.mark.timeout(5)
def test_category_reconnect_drains_full_queue_and_inflight_publication(monkeypatch):
    transport = public_state_transport()
    transport._queue = Queue(maxsize=1)
    transport._queue.put({"event": "first"})
    transport._state.metadata.return_value = {"livestream": {"categories": [{"id": 7}]}}
    entering_put = Event()
    original_put = transport._put

    def put(item):
        entering_put.set()
        original_put(item)

    reads = []

    def frames(_transport):
        reads.append("inflight")
        yield {"event": "second"}
        reads.append("unexpected extra read")

    monkeypatch.setattr(transport, "_put", put)
    monkeypatch.setattr(pt, "read_frames", frames)
    child = MagicMock()
    transport._transports.append(child)
    worker = Thread(target=transport._read, args=(child,))
    transport._workers.append(worker)
    worker.start()
    try:
        assert entering_put.wait(5)
        transport._poll()
        assert transport.recv()["event"] == "kick:public_state"
        assert transport.recv()["event"] == "kick:viewer_count"
        assert transport.recv()["event"] == "first"
        worker.join(5)
        assert not worker.is_alive()
        assert transport.recv()["event"] == "second"
        with pytest.raises(ConnectionError, match="category feeds changed"):
            transport.recv()
        assert reads == ["inflight"]
    finally:
        transport.close()


def test_category_reconnect_preserves_terminal_reader_error_after_publications(
    monkeypatch,
):
    transport = public_state_transport()
    transport._state = None
    transport._queue.put({"event": "accepted"})
    transport._request_reconnect(ConnectionError("category change"))
    monkeypatch.setattr(
        pt, "read_frames", MagicMock(side_effect=CaptchaChallengeRequired("blocked"))
    )
    child = MagicMock()
    transport._read(child)
    transport._request_reconnect(ConnectionError("secondary disconnect"))
    assert transport.recv()["event"] == "accepted"
    with pytest.raises(CaptchaChallengeRequired, match="blocked"):
        transport.recv()
    transport.close()


def test_reconnect_rechecks_publications_after_reader_exits_during_queue_timeout():
    class LateQueue(Queue):
        late = True

        def get(self, block=True, timeout=None):
            if timeout is not None and self.late:
                self.late = False
                self.put_nowait({"event": "last accepted publication"})
                raise Empty
            return super().get(block=block, timeout=timeout)

    transport = public_state_transport()
    transport._state = None
    transport._queue = LateQueue()
    transport._request_reconnect(ConnectionError("category change"))
    assert transport.recv() == {"event": "last accepted publication"}
    for _ in range(2):
        with pytest.raises(ConnectionError, match="category change"):
            transport.recv()
    transport.close()


@pytest.mark.parametrize(
    ("trust_env", "configured", "expected"),
    [
        (True, None, [None, "http://environment.test:8000"]),
        (False, None, [None, None]),
        (
            True,
            {"https": "http://configured.test:8000"},
            ["http://configured.test:8000"] * 2,
        ),
    ],
)
def test_public_proxy_resolution_honors_each_negotiated_host(
    monkeypatch, trust_env, configured, expected
):
    monkeypatch.setenv("HTTPS_PROXY", "http://environment.test:8000")
    monkeypatch.setenv("NO_PROXY", ".kick.com")
    state = MagicMock()
    state.metadata.return_value = {}
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
    clients = [MagicMock(), MagicMock()]
    clients[0].negotiate.return_value = rc.RealtimeConnection(
        "centrifugo", "wss://realtime.us-east-1.platform.kick.com/connection/websocket"
    )
    clients[1].negotiate.return_value = rc.RealtimeConnection(
        "pusher", "wss://ws-us2.pusher.com/app/key"
    )
    factory = MagicMock(side_effect=clients)
    monkeypatch.setattr(pt, "KickRealtimeClient", factory)
    clients[0].connection_token.return_value = "anonymous-token"
    sockets = [FakeWebSocket(), FakeWebSocket()]
    connector = MagicMock(side_effect=sockets)
    monkeypatch.setattr(pt, "_default_connector", connector)
    transport = pt.KickPublicTransport(
        username="slug", channel_id="123", proxy=configured, trust_env=trust_env
    )
    transport.connect(1)
    assert [call.kwargs["proxy_url"] for call in connector.call_args_list] == expected
    assert all(
        call.kwargs["origin"] == "https://kick.com" for call in connector.call_args_list
    )
    assert all(call.kwargs["proxy"] == configured for call in factory.call_args_list)
    assert all(call.kwargs["trust_env"] == trust_env for call in factory.call_args_list)
    transport.close()
    assert all(socket.closed for socket in sockets)


def test_shared_socket_cancellation_and_explicit_origin(monkeypatch):
    transport = KickPusherTransport()
    ws = MagicMock()
    transport._ws = ws
    ws.abort.side_effect = OSError("already closed")
    transport.request_stop()
    ws.abort.assert_called_once()
    from chat_downloader.sites.kick import websocket_transport as wt

    monkeypatch.setattr(wt, "open_proxied_tls_socket", MagicMock())
    connector = MagicMock()
    monkeypatch.setattr(wt, "create_connection", connector)
    wt._default_connector("wss://example.test/", 1, origin="https://kick.com")


@pytest.mark.parametrize("stage", ["before", "metadata", "negotiate", "socket"])
def test_cancellation_during_setup_closes_late_resources(monkeypatch, stage):
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    state = MagicMock()
    clients = [MagicMock(), MagicMock()]
    descriptor = rc.RealtimeConnection("pusher", "wss://ws-us2.pusher.com/app/key")
    for client in clients:
        client.negotiate.return_value = descriptor
    socket = FakeWebSocket()
    connector = MagicMock(return_value=socket)
    state_factory = MagicMock(return_value=state)
    factory = MagicMock(side_effect=clients)
    monkeypatch.setattr(pt, "KickPublicState", state_factory)
    monkeypatch.setattr(pt, "KickRealtimeClient", factory)
    monkeypatch.setattr(pt, "_default_connector", connector)

    def cancel(value):
        transport.request_stop()
        return value

    state.metadata.return_value = {}
    if stage == "before":
        transport.request_stop()
    elif stage == "metadata":
        state.metadata.side_effect = lambda *_: cancel({})
    elif stage == "negotiate":
        clients[0].negotiate.side_effect = lambda *_: cancel(descriptor)
    else:
        connector.side_effect = lambda *_a, **_kw: cancel(socket)

    with pytest.raises(ConnectionError, match="setup failed"):
        transport.connect(1)
    transport.close()
    clients[1].negotiate.assert_not_called()
    assert connector.call_count == (stage == "socket")
    assert socket.closed is (stage == "socket")
    assert state.close.call_count == (stage != "before")
    assert bool(clients[0].close.call_count) is (stage in {"negotiate", "socket"})


def test_worker_start_failure_keeps_http_cleanup_with_setup_owner(monkeypatch):
    state = MagicMock()
    state.metadata.return_value = {}
    clients = [MagicMock(), MagicMock()]
    for client in clients:
        client.negotiate.return_value = rc.RealtimeConnection(
            "pusher", "wss://ws-us2.pusher.com/app/key"
        )
    sockets = [FakeWebSocket(), FakeWebSocket()]
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
    monkeypatch.setattr(pt, "KickRealtimeClient", MagicMock(side_effect=clients))
    monkeypatch.setattr(pt, "_default_connector", MagicMock(side_effect=sockets))
    worker = MagicMock()
    worker.start.side_effect = RuntimeError("unable to start")
    monkeypatch.setattr(pt, "Thread", MagicMock(return_value=worker))
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    with pytest.raises(RuntimeError, match="unable to start"):
        live_service._open_subscribed_transport(
            FakeDownloader(), "456", ChatRequest(), lambda: transport
        )
    worker.join.assert_not_called()
    assert all(socket.closed for socket in sockets)
    assert all(client.close.call_count == 1 for client in clients)


def test_public_subscription_cancellation_prevents_other_feed_and_readers():
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    children = [MagicMock(), MagicMock()]
    transport._transports = children
    children[0].subscribe_channels.side_effect = lambda *_: transport.request_stop()
    with pytest.raises(ConnectionError, match="stopped"):
        transport.subscribe("456")
    children[1].subscribe_channels.assert_not_called()
    with pytest.raises(ConnectionError, match="stopped"):
        transport.set_timeout(1)
    assert all(child.set_timeout.call_count == 0 for child in children)
    transport.close()


@pytest.mark.parametrize("public_events", [False, True])
def test_default_live_capture_composes_negotiation_protocols_and_cancellation(
    monkeypatch,
    public_events,
):
    channel = {"id": 123, "chatroom": {"id": 456}, "livestream": None}
    state_session = FakeKickSession([FakeResponse(payload=channel)] * 2)
    worker_state_session = FakeKickSession([])
    worker_state_created = Event()

    def create_state_session(**_kwargs):
        if state_session.close_calls:
            worker_state_created.set()
            return worker_state_session
        return state_session

    sessions = [
        PostSession(
            [FakeResponse(payload=connection("pusher", app_key="key", cluster="us2"))]
        ),
        PostSession(
            [
                FakeResponse(
                    payload=connection(
                        url="wss://realtime.us-east-1.platform.kick.com/connection/websocket"
                    )
                ),
                FakeResponse(payload={"data": {"token": "anonymous-token"}}),
            ]
        ),
    ]
    monkeypatch.setattr(ps, "create_kick_session", create_state_session)
    monkeypatch.setattr(rc, "create_kick_session", MagicMock(side_effect=sessions))
    sockets = [
        BlockingSocket(
            [
                json.dumps({"event": PUSHER_CONNECTION_ESTABLISHED}),
                json.dumps(
                    {
                        "event": PUSHER_SUBSCRIPTION_SUCCEEDED,
                        "channel": "chatrooms.456.v2",
                    }
                ),
                json.dumps(
                    pusher_frame(
                        "App\\Events\\ChatMessageEvent", raw_message("composed")
                    )
                ),
            ]
        ),
        BlockingSocket(
            [
                json.dumps({"id": 1, "connect": {}}),
                *[
                    json.dumps({"id": number, "subscribe": {}})
                    for number in range(2, 7)
                ],
            ]
        ),
    ]
    connector = MagicMock(side_effect=sockets)
    monkeypatch.setattr(pt, "_default_connector", connector)
    opened = []

    def public_transport(**kwargs):
        transport = pt.KickPublicTransport(**kwargs)
        opened.append(transport)
        return transport

    monkeypatch.setattr(live_service, "KickPublicTransport", public_transport)
    downloader = FakeDownloader()
    downloader._kick_api_client = MagicMock()
    downloader._kick_api_client.fetch_channel.return_value = channel
    downloader._kick_api_client.fetch_preloaded_chat_state.return_value = None
    downloader._kick_api_client.fetch_message_page.return_value = {
        "data": {"messages": []}
    }
    chat = live_service.get_chat_by_channel(
        downloader,
        "slug",
        ChatRequest(message_groups=["all" if public_events else "messages"]),
    )
    try:
        for message in chat:
            if message.get("message_id") == "composed":
                break
        else:
            pytest.fail("negotiated chat record was not emitted")
        if public_events:
            assert worker_state_created.wait(1)
    finally:
        chat.close()
    assert sockets[0].closed
    assert sockets[1].closed is public_events
    assert state_session.close_calls == int(public_events)
    assert worker_state_session.close_calls == int(public_events)
    assert sessions[0].close_calls == 1
    assert sessions[1].close_calls == int(public_events)
    assert chat.diagnostics["pusher_connection_count"] == 1
    assert chat.diagnostics["centrifugo_connection_count"] == int(public_events)
    assert chat.diagnostics["public_state_poll_count"] == int(public_events)
    assert len(opened[0]._workers) == (2 if public_events else 1)
    assert (opened[0]._poll_worker is not None) is public_events
    assert len(state_session.calls) == int(public_events)
    expected_hosts = ["ws-us2.pusher.com"]
    if public_events:
        expected_hosts.append("realtime.us-east-1.platform.kick.com")
    assert [
        call.args[0].split("/")[2] for call in connector.call_args_list
    ] == expected_hosts
    assert connector.call_args.kwargs["origin"] == "https://kick.com"

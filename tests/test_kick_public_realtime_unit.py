# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import time
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.sites.kick import public_transport as pt
from chat_downloader.sites.kick import realtime_connection as rc
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
    FakeKickSession,
    FakeResponse,
    FakeWebSocket,
    load_fixture,
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
    transport = KickCentrifugoTransport(client=client)
    transport._url = "wss://realtime.us-east-1.platform.kick.com/connection/websocket"
    transport._connector = lambda *_a, **_kw: ws
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
    state = MagicMock()
    state.metadata.return_value = channel
    state.viewers.return_value = [{"livestream_id": 123, "viewers": 42}]
    monkeypatch.setattr(pt, "KickPublicState", MagicMock(return_value=state))
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
    )
    transport._proxy_url = "http://proxy.test"
    transport.connect(1, force_discover=True)
    transport.subscribe("456")
    transport.set_timeout(99)
    frames = []
    end = time.monotonic() + 2
    while (
        len([f for f in frames if f.get("event") == PUSHER_SUBSCRIPTION_SUCCEEDED]) < 7
        and time.monotonic() < end
    ):
        frame = transport.recv()
        if frame is not None:
            frames.append(frame)
    transport.send_pong()
    transport.close()
    transport.close()
    assert all(not worker.is_alive() for worker in transport._workers)
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


def public_state_transport():
    transport = pt.KickPublicTransport(username="slug", channel_id="123")
    transport._receive_timeout = 0.001
    transport._state = MagicMock()
    transport._state.metadata.return_value = {}
    transport._state.viewers.return_value = []
    return transport


def test_public_snapshots_only_emit_changed_values_and_restart_changed_categories():
    transport = public_state_transport()
    assert transport.recv()["event"] == "kick:public_state"
    assert transport.recv()["event"] == "kick:viewer_count"
    assert transport.recv() is None
    transport._next_poll = 0
    assert transport.recv() is None
    transport._state.viewers.return_value = [{"viewers": 42}]
    transport._next_poll = 0
    assert transport.recv()["event"] == "kick:viewer_count"
    transport._state.metadata.return_value = {"livestream": {"categories": [{"id": 7}]}}
    transport._next_poll = 0
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


def test_public_poll_failure_is_visible_and_does_not_drop_chat():
    transport = public_state_transport()
    transport._state.metadata.side_effect = RequestException("offline")
    counters = []
    transport._diagnostic_callback = counters.append
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
    monkeypatch.setattr(
        pt, "read_frames", MagicMock(side_effect=CaptchaChallengeRequired("blocked"))
    )
    child, client = MagicMock(), MagicMock()
    transport._read(child, client)
    assert isinstance(transport._queue.get(), CaptchaChallengeRequired)
    child.close.assert_called_once()
    client.close.assert_called_once()


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
    assert connector.call_args.kwargs["origin"] == "https://kick.com"

# SPDX-License-Identifier: MIT

"""Connection diagnostics must compose with recovery and remain content-free."""

from __future__ import annotations

import ssl
from unittest.mock import Mock

import pytest

from chat_downloader.runtime.capture_manifest import _manifest_diagnostics
from chat_downloader.sites.proxy import SSLTransport, tls_socket_diagnostics
from chat_downloader.sites.twitch import irc_diagnostics, irc_transport, live_service
from tests.test_twitch_live_service_unit import _downloader, _live, _request
from tests.twitch_third_helpers import irc_frame


@pytest.mark.parametrize("kind", [ssl.SSLSocket, SSLTransport])
@pytest.mark.parametrize("state", ["ok", "unknown", "closed", "empty"])
def test_tls_diagnostics_omit_unknown_and_unavailable_values(kind, state):
    transport = Mock(spec=kind)
    transport.version.return_value = "TLSv1.3" if state == "ok" else "PRIVATE"
    transport.cipher.return_value = (
        "TLS_AES_128_GCM_SHA256" if state == "ok" else "PRIVATE",
        "TLSv1.3",
        128,
    )
    if state == "closed":
        transport.version.side_effect = OSError("PRIVATE")
    if state == "empty":
        transport.version.return_value = None
        transport.cipher.return_value = None
    assert tls_socket_diagnostics(transport) == (
        {"tls_version": "TLSv1.3", "tls_cipher": "TLS_AES_128_GCM_SHA256"}
        if state == "ok"
        else {}
    )
    assert tls_socket_diagnostics(object()) == {}


def test_monotonic_outages_span_retries_and_finish_once(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(irc_diagnostics, "monotonic", lambda: now[0])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    diagnostics.start_outage()
    now[0] = 2.0
    diagnostics.start_outage()
    now[0] = 4.0
    diagnostics.finish_outage()
    diagnostics.finish_outage()
    diagnostics.start_outage()
    now[0] = 4.5
    diagnostics.finish_outage()
    diagnostics.record_duration("PRIVATE", 0)
    assert diagnostics.summary["reconnect_outage_duration_ms"] == 3500
    assert diagnostics.summary["reconnect_outage_max_duration_ms"] == 3000
    assert "PRIVATE_duration_ms" not in diagnostics.summary


def test_real_transport_recovery_records_tls_and_failed_setup(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(irc_diagnostics, "monotonic", lambda: now[0])
    frame = irc_frame("id=first;display-name=User;room-id=1;user-id=1", text="hello")
    sock = Mock(spec=ssl.SSLSocket)
    sock.version.return_value = "TLSv1.3"
    sock.cipher.return_value = ("TLS_AES_128_GCM_SHA256", "TLSv1.3", 128)
    sock.recv.side_effect = [frame.encode(), b""]

    def connect(*args):
        now[0] += 0.25
        return sock

    monkeypatch.setattr(irc_transport, "_create_irc_socket", connect)
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()
    # A failed reconnect setup is followed by a transport without TLS introspection.
    last = Mock(socket=object())
    attempts = [irc_transport.TwitchChatIRC, OSError("PRIVATE"), last]

    def factory(**kwargs):
        effect = attempts.pop(0)
        if isinstance(effect, OSError):
            now[0] += 0.5
            raise effect
        if callable(effect) and effect is irc_transport.TwitchChatIRC:
            return effect(**kwargs)
        now[0] += 0.25
        return effect

    def messages(irc, channel, request, badges):
        if isinstance(irc, irc_transport.TwitchChatIRC):
            yield from irc_transport.get_chat_messages_by_stream_id(
                irc, channel, request, badges, diagnostics=diagnostics
            )
        else:
            now[0] += 1
            yield {"message_type": "text_message", "message_id": "second"}

    def retry(*args, **kwargs):
        now[0] += 2

    source = _live(
        _downloader(retry=retry),
        _request(max_attempts=2),
        irc_factory=factory,
        message_generator=messages,
        diagnostics=diagnostics,
    )
    assert next(source)["message_id"] == "first"
    assert diagnostics.summary["irc_tls_cipher"] == "TLS_AES_128_GCM_SHA256"
    assert next(source)["message_id"] == "second"
    source.close()
    assert diagnostics.summary["connection_attempt_count"] == 3
    assert diagnostics.summary["connection_setup_failure_count"] == 1
    assert diagnostics.summary["connection_setup_duration_ms"] == 1000
    assert diagnostics.summary["connection_setup_max_duration_ms"] == 500
    assert diagnostics.summary["reconnect_outage_duration_ms"] == 3750
    assert "irc_tls_cipher" not in diagnostics.summary
    assert "irc_tls_version" not in diagnostics.summary
    assert "PRIVATE" not in repr(diagnostics.summary)


def test_unrecovered_outage_is_recorded_on_shutdown(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(irc_diagnostics, "monotonic", lambda: now[0])
    diagnostics = irc_diagnostics._TwitchLiveDiagnostics()

    def messages(*args):
        raise ConnectionError("PRIVATE")
        yield  # pragma: no cover - retain generator shape for the injected failure

    def wait(*args, **kwargs):
        now[0] = 2.5
        raise KeyboardInterrupt

    monkeypatch.setattr(live_service, "wait_for_reconnect", wait)
    with pytest.raises(KeyboardInterrupt):
        list(
            _live(
                irc_factory=Mock(return_value=Mock()),
                message_generator=messages,
                diagnostics=diagnostics,
            )
        )
    assert diagnostics.summary["reconnect_outage_duration_ms"] == 2500


def test_manifest_filters_connection_diagnostics():
    state = {
        "irc_tls_version": "TLSv1.3",
        "irc_tls_cipher": "TLS_AES_128_GCM_SHA256",
        "connection_setup_duration_ms": 100,
        "reconnect_outage_duration_ms": True,
        "optional_metadata_primary_team_service_error_count": 1,
        "private_peer": "PRIVATE",
    }
    assert _manifest_diagnostics(state) == {
        key: value
        for key, value in state.items()
        if key not in {"private_peer", "reconnect_outage_duration_ms"}
    }
    assert (
        _manifest_diagnostics({"irc_tls_version": "PRIVATE", "irc_tls_cipher": []})
        == {}
    )

# SPDX-License-Identifier: MIT

"""Offline deterministic tests for retry policy and HTTP timeout construction.

Covers:
- backoff_seconds() formula (unified across Twitch + YouTube)
- HTTP timeout tuple built from connect_timeout / read_timeout params
- Twitch IRC create_connection retries on OSError
"""

from __future__ import annotations

import contextlib
from typing import NoReturn
from unittest.mock import MagicMock, patch

import pytest

from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.utils.conversion_utils import backoff_seconds
from chat_downloader.utils.retry_utils import RetryPolicy

# ---------------------------------------------------------------------------
# backoff_seconds (Fix 2 — unified retry policy)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "retry_timeout", "expected"),
    [
        (1, None, 0.0),  # first retry: no sleep
        (2, None, 1.0),  # second retry: 2^0 = 1 s
        (3, None, 2.0),  # third retry:  2^1 = 2 s
        (4, None, 4.0),  # fourth retry: 2^2 = 4 s
        (5, None, 8.0),  # fifth retry:  2^3 = 8 s
        (1, 0.5, 0.5),  # fixed 0.5 s regardless of attempt
        (3, 0.5, 0.5),  # fixed 0.5 s regardless of attempt
        (1, 0.0, 0.0),  # fixed 0 s (non-blocking fixed)
    ],
)
def test_backoff_seconds_formula(attempt, retry_timeout, expected) -> None:
    with patch(
        "chat_downloader.utils.conversion_utils._SYSTEM_RANDOM.uniform",
        return_value=0.0,
    ):
        assert backoff_seconds(attempt, retry_timeout) == pytest.approx(expected)


def test_backoff_seconds_returns_float() -> None:
    result = backoff_seconds(2, None)
    assert isinstance(result, float)


def test_retry_policy_sleep_seconds_matches_backoff_formula() -> None:
    policy = RetryPolicy(max_attempts=5, retry_timeout=None)
    with patch(
        "chat_downloader.utils.conversion_utils._SYSTEM_RANDOM.uniform",
        return_value=0.0,
    ):
        assert policy.sleep_seconds(1) == pytest.approx(0.0)
        assert policy.sleep_seconds(2) == pytest.approx(1.0)
        assert policy.sleep_seconds(3) == pytest.approx(2.0)


def test_exponential_backoff_is_capped_for_large_attempt_numbers() -> None:
    with patch(
        "chat_downloader.utils.conversion_utils._SYSTEM_RANDOM.uniform",
        return_value=0.0,
    ):
        assert backoff_seconds(10_000, None) == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# HTTP timeout tuple (Fix 1 — typed timeouts)
# ---------------------------------------------------------------------------


def test_base_chat_downloader_default_http_timeout() -> None:
    """Default connect/read timeout is (10, 30) matching _DEFAULT_TIMEOUT."""
    d = BaseChatDownloader()
    assert d._http_timeout == (10.0, 30.0)


def test_base_chat_downloader_custom_http_timeout() -> None:
    """Custom connect_timeout and read_timeout are stored on the instance."""
    d = BaseChatDownloader(connect_timeout=5.0, read_timeout=60.0)
    assert d._http_timeout == (5.0, 60.0)


def test_session_get_injects_http_timeout(monkeypatch, make_fake_http_response) -> None:
    """_session_get uses self._http_timeout, not the hardcoded constant."""
    d = BaseChatDownloader(connect_timeout=3.0, read_timeout=15.0)

    captured = {}

    def fake_get(url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return make_fake_http_response(200, {})

    monkeypatch.setattr(d.session, "get", fake_get)
    d._session_get("http://example.com")
    assert captured["timeout"] == (3.0, 15.0)


def test_session_post_injects_http_timeout(
    monkeypatch, make_fake_http_response
) -> None:
    """_session_post uses self._http_timeout, not the hardcoded constant."""
    d = BaseChatDownloader(connect_timeout=7.0, read_timeout=20.0)

    captured = {}

    def fake_post(url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return make_fake_http_response(200, {})

    monkeypatch.setattr(d.session, "post", fake_post)
    d._session_post("http://example.com")
    assert captured["timeout"] == (7.0, 20.0)


# ---------------------------------------------------------------------------
# Twitch IRC create_connection OSError retry (Fix 1 from previous session)
# ---------------------------------------------------------------------------


def test_twitch_irc_create_connection_retries_on_oserror() -> None:
    """create_connection in the Twitch extractor retries on OSError."""
    from chat_downloader.errors import RetriesExceeded
    from chat_downloader.sites.twitch.extractor import TwitchChatDownloader

    downloader = TwitchChatDownloader()

    call_counts = {"n": 0}

    def always_fail(*args, **kwargs) -> NoReturn:
        call_counts["n"] += 1
        msg = "network unreachable"
        raise OSError(msg)

    params = {
        "max_attempts": 3,
        "retry_timeout": 0,  # instant retry for test speed
        "interruptible_retry": False,
        "message_receive_timeout": 0.1,
        "message_groups": [],
    }

    with patch(
        "chat_downloader.sites.twitch.extractor.TwitchChatIRC",
        side_effect=always_fail,
    ):
        gen = downloader._get_chat_messages_by_stream_id("testchan", params)
        with pytest.raises(RetriesExceeded):
            next(gen)

    assert call_counts["n"] == 3


def test_twitch_irc_create_connection_succeeds_after_transient_oserror() -> None:
    """create_connection recovers when OSError clears on a later attempt."""
    from chat_downloader.sites.twitch.extractor import TwitchChatDownloader

    downloader = TwitchChatDownloader()

    call_counts = {"n": 0}

    def flaky_irc(*args, **kwargs):
        call_counts["n"] += 1
        if call_counts["n"] < 2:
            msg = "transient failure"
            raise OSError(msg)
        mock_irc = MagicMock()
        mock_irc.join_channel = MagicMock()
        mock_irc.close_connection = MagicMock()
        return mock_irc

    params = {
        "max_attempts": 3,
        "retry_timeout": 0,
        "interruptible_retry": False,
        "message_receive_timeout": 0.1,
        "message_groups": [],
    }

    # Patch both the IRC class (for create_connection) and the message generator
    # so the generator exits immediately after a successful connection.
    with (
        patch(
            "chat_downloader.sites.twitch.extractor.TwitchChatIRC",
            side_effect=flaky_irc,
        ),
        patch(
            "chat_downloader.sites.twitch.extractor.get_chat_messages_by_stream_id",
            return_value=iter([]),
        ),
    ):
        gen = downloader._get_chat_messages_by_stream_id("testchan", params)
        result = list(gen)

    assert result == []
    assert call_counts["n"] == 2  # first attempt failed, second succeeded


# ---------------------------------------------------------------------------
# Structured retry debug context (Item 6)
# ---------------------------------------------------------------------------


def test_retry_log_includes_attempt_over_max_attempts(caplog) -> None:
    """Retry() warning message must contain 'attempt/max_attempts' format."""
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="chat_downloader"),
        contextlib.suppress(Exception),
    ):
        BaseChatDownloader.retry(
            attempt_number=2,
            max_attempts=5,
            retry_timeout=0,
            interruptible_retry=False,
        )

    messages = " ".join(r.message for r in caplog.records)
    assert "2/5" in messages, f"Expected '2/5' in retry log; got: {messages!r}"


def test_retry_log_includes_exception_type(caplog) -> None:
    """Retry() must include the exception class name in its log message."""
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="chat_downloader"),
        contextlib.suppress(Exception),
    ):
        BaseChatDownloader.retry(
            attempt_number=1,
            max_attempts=3,
            error=ConnectionError("no route"),
            retry_timeout=0,
            interruptible_retry=False,
        )

    messages = " ".join(r.message for r in caplog.records)
    assert "ConnectionError" in messages


# ---------------------------------------------------------------------------
# Twitch IRC socket timeout and connect-timeout wiring (Item 9)
# ---------------------------------------------------------------------------


def test_twitch_irc_connect_uses_10_second_timeout() -> None:
    """TwitchChatIRC.__init__ must call TLS connect with timeout=10."""
    from chat_downloader.sites.twitch.irc_transport import TwitchChatIRC

    captured = {}
    wrapped_sockets = []

    def fake_create_connection(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return MagicMock(name="raw_socket")

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            captured["wrapped_server_hostname"] = server_hostname
            wrapped = MagicMock(name="wrapped_socket")
            wrapped.settimeout = MagicMock()
            wrapped.sendall = MagicMock()
            wrapped_sockets.append(wrapped)
            return wrapped

    with (
        patch(
            "chat_downloader.sites.twitch.irc_transport.socket.create_connection",
            fake_create_connection,
        ),
        patch(
            "chat_downloader.sites.twitch.irc_transport.ssl.create_default_context",
            return_value=FakeContext(),
        ),
    ):
        TwitchChatIRC()

    assert captured["address"] == ("irc.chat.twitch.tv", 6697)
    assert captured["timeout"] == 10, (
        f"Expected connect timeout=10, got {captured['timeout']!r}"
    )
    assert captured["wrapped_server_hostname"] == "irc.chat.twitch.tv"
    assert wrapped_sockets, "Expected TLS wrapper to produce a wrapped socket"


def test_twitch_irc_connect_resets_to_blocking_after_connect() -> None:
    """After TLS connect, the wrapped socket must switch to blocking mode."""
    from chat_downloader.sites.twitch.irc_transport import TwitchChatIRC

    settimeout_calls = []

    def fake_create_connection(address, timeout):
        return MagicMock(name="raw_socket")

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            mock_sock = MagicMock(name="wrapped_socket")
            mock_sock.settimeout = settimeout_calls.append
            mock_sock.sendall = MagicMock()
            return mock_sock

    with (
        patch(
            "chat_downloader.sites.twitch.irc_transport.socket.create_connection",
            fake_create_connection,
        ),
        patch(
            "chat_downloader.sites.twitch.irc_transport.ssl.create_default_context",
            return_value=FakeContext(),
        ),
    ):
        TwitchChatIRC()

    assert None in settimeout_calls, (
        "Expected settimeout(None) call to reset to blocking mode"
    )


def test_twitch_irc_set_timeout_delegates_to_socket() -> None:
    """set_timeout() must call socket.settimeout() with the given value."""
    from chat_downloader.sites.twitch.irc_transport import TwitchChatIRC

    settimeout_calls = []

    def fake_create_connection(address, timeout):
        return MagicMock(name="raw_socket")

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            mock_sock = MagicMock(name="wrapped_socket")
            mock_sock.settimeout = settimeout_calls.append
            mock_sock.sendall = MagicMock()
            return mock_sock

    with (
        patch(
            "chat_downloader.sites.twitch.irc_transport.socket.create_connection",
            fake_create_connection,
        ),
        patch(
            "chat_downloader.sites.twitch.irc_transport.ssl.create_default_context",
            return_value=FakeContext(),
        ),
    ):
        irc = TwitchChatIRC()

    settimeout_calls.clear()
    irc.socket.settimeout = settimeout_calls.append
    irc.set_timeout(0.5)

    assert 0.5 in settimeout_calls, (
        f"Expected set_timeout(0.5) to call socket.settimeout(0.5); "
        f"calls={settimeout_calls}"
    )

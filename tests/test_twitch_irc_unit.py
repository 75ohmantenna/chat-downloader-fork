# SPDX-License-Identifier: MIT

"""Unit tests for pure IRC buffer-processing helpers in irc_transport.py."""

import contextlib
import re
import time
from unittest.mock import Mock

from chat_downloader.sites.twitch.irc_transport import (
    _create_irc_socket,
    _process_irc_buffer,
    _should_send_keepalive,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Simple pattern that matches lines ending with \r\n, capturing the full line.
# Used to drive _process_irc_buffer independently from MESSAGE_REGEX.
_LINE_PATTERN: re.Pattern[str] = re.compile(r"[^\r\n]+\r\n", re.MULTILINE)

# Minimal Twitch PRIVMSG line that matches MESSAGE_REGEX.
_TAG = "@badge-info=;badges=;color=;display-name=User;emotes=;id=1;mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;user-id=1;user-type="  # noqa: E501
_PRIVMSG_TEMPLATE = (
    "{tag} :user!user@user.tmi.twitch.tv PRIVMSG #example :{text}"
)


def _privmsg(text: str) -> str:
    """Return a syntactically valid Twitch IRC PRIVMSG line (without CRLF)."""
    return _PRIVMSG_TEMPLATE.format(tag=_TAG, text=text)


# ---------------------------------------------------------------------------
# _process_irc_buffer — complete buffer (no partial match)
# ---------------------------------------------------------------------------


def test_process_irc_buffer_complete_buffer_returns_all_matches() -> None:
    r"""A buffer ending with \r\n yields all matches and an empty remainder."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    line1 = _privmsg("hello") + "\r\n"
    line2 = _privmsg("world") + "\r\n"
    buf = line1 + line2

    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    assert len(matches) == 2, "Expected two complete matches"
    assert remaining == "", "No bytes should remain after a complete buffer"


def test_process_irc_buffer_complete_buffer_single_line() -> None:
    """A single complete IRC line returns one match and empty remainder."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    buf = _privmsg("single") + "\r\n"
    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    assert len(matches) == 1
    assert remaining == ""


# ---------------------------------------------------------------------------
# _process_irc_buffer — buffer ending mid-match (partial last line)
# ---------------------------------------------------------------------------


def test_process_irc_buffer_partial_last_line_drops_incomplete_match() -> None:
    """A mid-line buffer end drops the last match but keeps its start."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    complete_line = _privmsg("first") + "\r\n"
    # Second message is deliberately cut off — no trailing \r\n.
    partial_line = _privmsg("second_partial")
    buf = complete_line + partial_line

    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    # Only the first, complete line should be returned.
    assert len(matches) == 1, "Partial match must be excluded from results"
    # The remainder must begin at the start of the partial match so it can
    # be prepended to the next recv() chunk.
    assert remaining.startswith("@"), (
        "Remainder should start at the partial match's @"
    )
    assert "second_partial" in remaining


def test_process_irc_buffer_single_partial_line_yields_no_matches() -> None:
    """A partial-only first match returns no matches and the full buffer."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    buf = _privmsg("only_partial")  # no \r\n — the line is still arriving
    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    assert matches == [], (
        "No complete matches expected for a pure partial buffer"
    )
    assert remaining == buf, (
        "Full buffer must be returned for prepending to next chunk"
    )


def test_process_irc_buffer_empty_buffer_returns_empty() -> None:
    """An empty buffer yields no matches and an empty remainder."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    remaining, matches = _process_irc_buffer("", MESSAGE_REGEX)

    assert matches == []
    assert remaining == ""


def test_process_irc_buffer_no_matching_lines_complete_buffer() -> None:
    """A complete buffer with no matches returns empty matches and remainder."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    # Housekeeping IRC traffic that MESSAGE_REGEX does not match.
    buf = "PING :tmi.twitch.tv\r\n"
    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    assert matches == []
    assert remaining == ""


def test_process_irc_buffer_no_matching_lines_incomplete_buffer() -> None:
    """A partial buffer with no matches carries the full string forward."""
    from chat_downloader.sites.twitch.constants import MESSAGE_REGEX

    buf = "PING :tmi.twitch"  # truncated, no \r\n
    remaining, matches = _process_irc_buffer(buf, MESSAGE_REGEX)

    assert matches == []
    assert remaining == buf


# ---------------------------------------------------------------------------
# _process_irc_buffer — with a simple line-ending pattern for clarity
# ---------------------------------------------------------------------------


def test_process_irc_buffer_simple_pattern_complete() -> None:
    """Verify remainder and match count with a simple CRLF pattern."""
    buf = "line1\r\nline2\r\n"
    remaining, matches = _process_irc_buffer(buf, _LINE_PATTERN)

    assert len(matches) == 2
    assert remaining == ""


def test_process_irc_buffer_simple_pattern_partial_last() -> None:
    """The partial second line is dropped from matches and kept as remainder."""
    buf = "line1\r\npartial"
    remaining, matches = _process_irc_buffer(buf, _LINE_PATTERN)

    assert len(matches) == 1
    assert matches[0].group(0) == "line1\r\n"
    # The partial line contributes to the remainder so it can be retried.
    assert "partial" in remaining


# ---------------------------------------------------------------------------
# _should_send_keepalive
# ---------------------------------------------------------------------------


def test_should_send_keepalive_returns_true_when_interval_elapsed() -> None:
    """Returns True when more than ping_every seconds have passed."""
    now = time.time()
    last_ping = now - 61.0
    assert _should_send_keepalive(now, last_ping, 60.0) is True


def test_should_send_keepalive_returns_false_when_interval_not_elapsed() -> (
    None
):
    """Returns False when fewer than ping_every seconds have passed."""
    now = time.time()
    last_ping = now - 1.0
    assert _should_send_keepalive(now, last_ping, 60.0) is False


def test_should_send_keepalive_boundary_just_over() -> None:
    """Returns True at exactly one microsecond over the interval."""
    now = time.time()
    last_ping = now - 60.001
    assert _should_send_keepalive(now, last_ping, 60.0) is True


# ---------------------------------------------------------------------------
# _create_irc_socket
# ---------------------------------------------------------------------------


def test_create_irc_socket_wraps_raw_socket_and_uses_tls_defaults(
    monkeypatch,
) -> None:
    """Verify ``_create_irc_socket`` wraps the raw socket with SSL."""
    raw_socket = Mock()
    wrapped_socket = Mock()

    mock_ctx = Mock()
    mock_ctx.wrap_socket = Mock(return_value=wrapped_socket)

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_transport.socket.create_connection",
        lambda address, timeout: raw_socket,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_transport.ssl.create_default_context",
        lambda: mock_ctx,
    )

    result = _create_irc_socket()

    assert result is wrapped_socket
    mock_ctx.wrap_socket.assert_called_once_with(
        raw_socket,
        server_hostname="irc.chat.twitch.tv",
    )


def test_create_irc_socket_closes_raw_socket_if_ssl_wrap_fails(
    monkeypatch,
) -> None:
    """Raw socket must be closed when SSL wrapping raises."""
    raw_socket = Mock()
    mock_ctx = Mock()
    mock_ctx.wrap_socket = Mock(side_effect=RuntimeError("tls-fail"))

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_transport.socket.create_connection",
        lambda address, timeout: raw_socket,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.irc_transport.ssl.create_default_context",
        lambda: mock_ctx,
    )

    with contextlib.suppress(RuntimeError):
        _create_irc_socket()

    raw_socket.close.assert_called_once_with()

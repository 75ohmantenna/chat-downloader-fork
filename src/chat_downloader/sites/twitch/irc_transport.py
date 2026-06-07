# SPDX-License-Identifier: MIT

"""Low-level Twitch IRC transport helpers."""

from __future__ import annotations

import re
import socket
import ssl
import time
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log

from .constants import (
    IRC_ANONYMOUS_NICK,
    IRC_ANONYMOUS_PASSWORD,
    IRC_CAP_REQUEST,
    IRC_HOST,
    IRC_PORT,
    MESSAGE_REGEX,
    PING_TEXT,
    PONG_TEXT,
)
from .parsing.messages import _parse_irc_item

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest

    from .types import BadgeSet

_PROGRESS_LOG_INTERVAL_MESSAGES = 250
_READBUFFER_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
_PING_INTERVAL_SECONDS = 60
_CRLF_LENGTH = 2


def _create_irc_socket() -> ssl.SSLSocket:
    """Create and return the Twitch IRC TLS socket."""
    raw_socket = socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
    try:
        context = ssl.create_default_context()
        return context.wrap_socket(raw_socket, server_hostname=IRC_HOST)
    except (ssl.SSLError, OSError):
        raw_socket.close()
        raise


def _maybe_send_keepalive(
    irc: Any,
    current_time: float,
    last_ping_time: float,
    ping_every: float,
) -> float:
    """Send IRC keepalive if needed and return the updated last-ping time."""
    if not _should_send_keepalive(current_time, last_ping_time, ping_every):
        return last_ping_time
    try:
        irc.send_raw("PING")
    except OSError as e:
        raise ConnectionError("Lost connection while sending PING.") from e
    return current_time


def _process_irc_buffer(
    readbuffer: str,
    pattern: re.Pattern[str],
) -> tuple[str, list[re.Match[str]]]:
    r"""Run ``pattern`` over ``readbuffer`` and handle partial trailing matches.

    The IRC stream is received in arbitrary chunks.  A match may be split
    across two consecutive ``recv`` calls, so the last regex match is only
    considered complete when the buffer ends with the IRC line terminator
    ``\r\n``.  If the buffer ends mid-line the last incomplete match is
    dropped from the returned list and the remainder of the buffer starting
    at that match's position is returned so it can be prepended to the next
    chunk.

    Args:
        readbuffer: The current accumulated receive buffer as a string.
        pattern: Compiled regex used to find IRC message lines.

    Returns:
        A 2-tuple of ``(remaining_buffer, matches)`` where
        ``remaining_buffer`` is the unconsumed tail that must be carried
        forward, and ``matches`` is the list of complete match objects ready
        for parsing.
    """
    matches = list(pattern.finditer(readbuffer))
    full_readbuffer = readbuffer.endswith("\r\n")

    if matches:
        if not full_readbuffer:
            span = matches[-1].span()
            pass_on = readbuffer[span[0] :]

            if "\r\n" in pass_on:
                pass_on = pass_on[span[1] - span[0] :]
            else:
                matches.pop()

            remaining = pass_on
        else:
            remaining = ""
    else:
        remaining = "" if full_readbuffer else readbuffer

    return remaining, matches


def _consume_irc_buffer(
    readbuffer: str,
    pattern: re.Pattern[str],
) -> tuple[str, list[re.Match[str]], str | None]:
    """Return complete IRC matches plus any unmatched buffer to log."""
    buffer_before = readbuffer
    readbuffer, matches = _process_irc_buffer(readbuffer, pattern)

    unmatched_full_buffer: str | None = None
    if not matches and buffer_before.endswith("\r\n"):
        unmatched_full_buffer = buffer_before

    return readbuffer, matches, unmatched_full_buffer


def _parse_irc_matches(
    matches: list[re.Match[str]],
    badge_set: BadgeSet | None,
    message_count: int,
) -> tuple[list[dict[str, Any]], int]:
    """Parse IRC matches and update the running message count."""
    items: list[dict[str, Any]] = []
    for match in matches:
        items.append(_parse_irc_item(match, badge_set))
        message_count += 1
    return items, message_count


def _should_send_keepalive(
    current_time: float, last_ping_time: float, ping_every: float
) -> bool:
    """Return ``True`` when enough time has elapsed to send a keepalive PING.

    The caller is responsible for sampling ``time.time()`` once and passing
    it as ``current_time``.  This keeps the function pure and avoids a second
    ``time.time()`` call per loop iteration.

    Args:
        current_time: The caller's snapshot of ``time.time()``.
        last_ping_time: Unix timestamp of the most recent PING send.
        ping_every: Interval in seconds between keepalive PINGs.

    Returns:
        ``True`` if ``ping_every`` seconds have passed since ``last_ping_time``.
    """
    return current_time - last_ping_time > ping_every


def _is_benign_unmatched_irc_buffer(readbuffer: str) -> bool:
    """Return True for unmatched IRC traffic that is expected and noisy."""
    lines = [line.strip() for line in readbuffer.splitlines() if line.strip()]
    if not lines:
        return True

    for line in lines:
        if line.startswith(("PING :", "PONG :")):
            continue

        if " JOIN #" in line or " PART #" in line:
            continue

        if "tmi.twitch.tv" not in line:
            return False

        parts = line.split()
        if len(parts) >= 3 and parts[1].isdigit():
            continue

        if (
            len(parts) >= 4
            and parts[1] == "CAP"
            and parts[2] == "*"
            and parts[3] == "ACK"
        ):
            continue

        return False

    return True


class TwitchChatIRC:
    """IRC socket connection manager for Twitch chat."""

    def __init__(self) -> None:
        """Open a socket and perform anonymous Twitch IRC setup."""
        self.socket = _create_irc_socket()
        self.socket.settimeout(None)
        self.current_channel: str | None = None

        try:
            self.send_raw(IRC_CAP_REQUEST)
            self.send_raw(f"PASS {IRC_ANONYMOUS_PASSWORD}")
            self.send_raw(f"NICK {IRC_ANONYMOUS_NICK}")
        except OSError:
            self.socket.close()
            raise

    def send_raw(self, string: str) -> None:
        """Send a raw IRC command followed by CRLF.

        Args:
            string: IRC command string without the trailing CRLF.
        """
        self.socket.sendall((string + "\r\n").encode("utf-8"))

    def recv(self, buffer_size: int) -> str:
        """Receive up to ``buffer_size`` bytes from the socket as a string.

        Args:
            buffer_size: Maximum number of bytes to receive.

        Returns:
            Decoded UTF-8 string with undecodable bytes ignored.
        """
        return self.socket.recv(buffer_size).decode("utf-8", "ignore")

    def join_channel(self, channel_name: str) -> None:
        """Join the given Twitch IRC channel if not already joined.

        Args:
            channel_name: Channel name (with or without leading ``#``).
        """
        channel_lower = channel_name.lower()
        if self.current_channel != channel_lower:
            self.send_raw(f"JOIN #{channel_lower}")
            self.current_channel = channel_lower

    def set_timeout(self, message_receive_timeout: float) -> None:
        """Set the socket receive timeout.

        Args:
            message_receive_timeout: Timeout in seconds; ``None`` means
                blocking.
        """
        self.socket.settimeout(message_receive_timeout)

    def close_connection(self) -> None:
        """Send QUIT and shut down the IRC socket."""
        try:
            self.send_raw("QUIT")
            self.socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        finally:
            self.socket.close()


def get_chat_messages_by_stream_id(
    irc: TwitchChatIRC,
    channel: str,
    params: ChatRequest | dict[str, Any],
    badge_set: BadgeSet | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield live chat messages for a stream via IRC."""
    from chat_downloader.models import ChatRequest

    request = (
        params
        if isinstance(params, ChatRequest)
        else ChatRequest.from_kwargs(**params)
    )
    buffer_size = request.buffer_size

    last_ping_time = time.time()
    ping_every = _PING_INTERVAL_SECONDS

    readbuffer = ""
    message_count = 0
    while True:
        try:
            new_info = irc.recv(buffer_size)

            if not new_info:
                msg = "Lost connection, reconnecting."
                raise ConnectionError(msg)

            readbuffer += new_info

            if len(readbuffer) > _READBUFFER_MAX_BYTES:
                log(
                    "warning",
                    f"IRC read buffer exceeded {_READBUFFER_MAX_BYTES} "
                    "bytes; discarding to prevent unbounded growth.",
                )
                last_crlf = readbuffer.rfind("\r\n")
                readbuffer = (
                    readbuffer[last_crlf + _CRLF_LENGTH :]
                    if last_crlf >= 0
                    else ""
                )

            if PING_TEXT in readbuffer:
                try:
                    irc.send_raw(PONG_TEXT)
                except OSError as e:
                    raise ConnectionError(
                        "Lost connection while sending PONG."
                    ) from e

            readbuffer, matches, unmatched_full_buffer = _consume_irc_buffer(
                readbuffer,
                MESSAGE_REGEX,
            )

            if matches:
                items, message_count = _parse_irc_matches(
                    matches,
                    badge_set,
                    message_count,
                )
                for data in items:
                    yield data
            elif unmatched_full_buffer is not None:
                # Buffer was fully consumed with no matches — log unrecognised
                # traffic.
                if not _is_benign_unmatched_irc_buffer(unmatched_full_buffer):
                    log(
                        "debug",
                        'No matches found in "\n'
                        f'{unmatched_full_buffer.strip()}\n"',
                    )

            current_time = time.time()
            last_ping_time = _maybe_send_keepalive(
                irc,
                current_time,
                last_ping_time,
                ping_every,
            )

        except TimeoutError:
            pass

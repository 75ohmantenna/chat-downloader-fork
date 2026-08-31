# SPDX-License-Identifier: MIT

"""Low-level Twitch IRC transport helpers."""

from __future__ import annotations

import codecs
import socket
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.proxy import _ProxySocket, open_proxied_tls_socket

from .constants import (
    IRC_ANONYMOUS_NICK,
    IRC_ANONYMOUS_PASSWORD,
    IRC_CAP_REQUEST,
    IRC_HOST,
    IRC_PORT,
    MESSAGE_REGEX,
    PONG_TEXT,
    TWITCH_DEBUG_SAMPLE_LIMIT,
)
from .irc_diagnostics import _is_benign_unmatched_irc_buffer
from .parsing.messages import _parse_irc_item

if TYPE_CHECKING:
    import re
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest

    from .irc_diagnostics import (
        _EventDiverseIrcFrameCapture,
        _SuccessfulIrcFrameCapture,
        _TwitchLiveDiagnostics,
    )
    from .types import BadgeSet

_PROGRESS_LOG_INTERVAL_MESSAGES = 250
_READBUFFER_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
_PING_INTERVAL_SECONDS = 60
_IDLE_WATCHDOG_SECONDS = 180
_MIN_RECEIVE_TIMEOUT_SECONDS = 1.0
_CRLF_LENGTH = 2


def _create_irc_socket(
    connect_timeout: float = 10.0,
    proxy_url: str | None = None,
) -> _ProxySocket:
    """Create and return the Twitch IRC TLS socket."""
    return open_proxied_tls_socket(
        IRC_HOST,
        IRC_PORT,
        timeout=connect_timeout,
        proxy_url=proxy_url,
    )


def _maybe_send_keepalive(
    irc: Any,
    current_time: float,
    last_ping_time: float,
    ping_every: float,
    diagnostics: _TwitchLiveDiagnostics | None = None,
) -> float:
    """Send IRC keepalive if needed and return the updated last-ping time."""
    if not _should_send_keepalive(current_time, last_ping_time, ping_every):
        return last_ping_time
    try:
        irc.send_raw("PING")
    except OSError as e:
        msg = "Lost connection while sending PING."
        raise ConnectionError(msg) from e
    if diagnostics is not None:
        diagnostics.increment("keepalive_ping_sent_count")
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
    successful_frame_capture: _SuccessfulIrcFrameCapture | None = None,
    event_frame_capture: _EventDiverseIrcFrameCapture | None = None,
    diagnostics: _TwitchLiveDiagnostics | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Parse IRC matches and update the running message count."""
    items: list[dict[str, Any]] = []
    for match in matches:
        item = _parse_irc_item(match, badge_set)
        raw_frame = f"{match.group(0)}\r\n"
        if successful_frame_capture is not None:
            successful_frame_capture.capture(raw_frame)
        if event_frame_capture is not None:
            event_frame_capture.capture(raw_frame, item, match.group(2), match.group(1))
        items.append(item)
        message_count += 1
        if diagnostics is not None:
            diagnostics.increment("parsed_irc_message_count")
    return items, message_count


def _should_send_keepalive(
    current_time: float, last_ping_time: float, ping_every: float
) -> bool:
    """Return whether the sampled monotonic time requires a keepalive PING."""
    return current_time - last_ping_time > ping_every


class TwitchChatIRC:
    """IRC socket connection manager for Twitch chat."""

    def __init__(
        self,
        *,
        connect_timeout: float = 10.0,
        proxy_url: str | None = None,
    ) -> None:
        """Open a socket and perform anonymous Twitch IRC setup."""
        self.socket = _create_irc_socket(connect_timeout, proxy_url)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self.socket.settimeout(None)
        self.current_channel: str | None = None
        self._closed = False

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
            Incrementally decoded UTF-8 text. Valid multibyte characters split
            across network chunks are preserved; malformed bytes are ignored.
        """
        data = self.socket.recv(buffer_size)
        return self._decoder.decode(data, final=not data)

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
            message_receive_timeout: Positive timeout in seconds.
        """
        self.socket.settimeout(message_receive_timeout)

    def close_connection(self) -> None:
        """Send QUIT and shut down the IRC socket."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.send_raw("QUIT")
            self.socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        finally:
            with suppress(OSError):
                self.socket.close()


def _drain_readbuffer(readbuffer: str) -> str:
    """Trim an oversized buffer to the last complete line."""
    log(
        "warning",
        f"IRC read buffer exceeded {_READBUFFER_MAX_BYTES} "
        "bytes; discarding to prevent unbounded growth.",
    )
    last_crlf = readbuffer.rfind("\r\n")
    return readbuffer[last_crlf + _CRLF_LENGTH :] if last_crlf >= 0 else ""


def _handle_ping(
    irc: TwitchChatIRC,
    completed_ping_count: int,
    diagnostics: _TwitchLiveDiagnostics | None = None,
) -> None:
    """Reply once per newly completed server PING frame."""
    for _ in range(completed_ping_count):
        try:
            irc.send_raw(PONG_TEXT)
        except OSError as e:
            msg = "Lost connection while sending PONG."
            raise ConnectionError(msg) from e
        if diagnostics is not None:
            diagnostics.increment("keepalive_pong_sent_count")


def _recv_irc(irc: TwitchChatIRC, buffer_size: int) -> str:
    """Receive one chunk, mapping socket failures into reconnect errors."""
    try:
        return irc.recv(buffer_size)
    except TimeoutError:
        raise
    except OSError as error:
        msg = "Twitch IRC receive failed; reconnecting."
        raise ConnectionError(msg) from error


def get_chat_messages_by_stream_id(
    irc: TwitchChatIRC,
    channel: str,  # noqa: ARG001 — part of the uniform transport callable signature
    params: ChatRequest | dict[str, Any],
    badge_set: BadgeSet | None = None,
    *,
    successful_frame_capture: _SuccessfulIrcFrameCapture | None = None,
    event_frame_capture: _EventDiverseIrcFrameCapture | None = None,
    diagnostics: _TwitchLiveDiagnostics | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield live chat messages for a stream via IRC."""
    from chat_downloader.models import ChatRequest

    request = (
        params if isinstance(params, ChatRequest) else ChatRequest.from_kwargs(**params)
    )
    if diagnostics is None:
        from .irc_diagnostics import _TwitchLiveDiagnostics

        diagnostics = _TwitchLiveDiagnostics()
    diagnostics.reset_transport_state()
    buffer_size = request.buffer_size

    last_receive_time = time.monotonic()
    last_ping_time = last_receive_time
    ping_every = _PING_INTERVAL_SECONDS

    readbuffer = ""
    message_count = 0
    while True:
        try:
            new_info = _recv_irc(irc, buffer_size)

            if not new_info:
                msg = "Lost connection, reconnecting."
                raise ConnectionError(msg)
            completed_ping_count = diagnostics.record_received_data(new_info)

            readbuffer += new_info

            if len(readbuffer) > _READBUFFER_MAX_BYTES:
                readbuffer = _drain_readbuffer(readbuffer)

            _handle_ping(irc, completed_ping_count, diagnostics)

            readbuffer, matches, unmatched_full_buffer = _consume_irc_buffer(
                readbuffer,
                MESSAGE_REGEX,
            )

            if matches:
                items, message_count = _parse_irc_matches(
                    matches,
                    badge_set,
                    message_count,
                    successful_frame_capture,
                    event_frame_capture,
                    diagnostics,
                )
                yield from items
            elif unmatched_full_buffer is not None:
                # Buffer was fully consumed with no matches — log unrecognized
                # traffic.
                if not _is_benign_unmatched_irc_buffer(unmatched_full_buffer):
                    capture_debug_sample(
                        "twitch-unknown-irc-shape",
                        {"raw": unmatched_full_buffer},
                        sample_limit=TWITCH_DEBUG_SAMPLE_LIMIT,
                    )
                    log(
                        "debug",
                        f'No matches found in "\n{unmatched_full_buffer.strip()}\n"',
                    )

            current_time = time.monotonic()
            last_receive_time = current_time
            last_ping_time = _maybe_send_keepalive(
                irc,
                current_time,
                last_ping_time,
                ping_every,
                diagnostics,
            )
        except TimeoutError:
            diagnostics.increment("receive_timeout_count")
            current_time = time.monotonic()
            last_ping_time = _maybe_send_keepalive(
                irc,
                current_time,
                last_ping_time,
                ping_every,
                diagnostics,
            )
            if current_time - last_receive_time >= _IDLE_WATCHDOG_SECONDS:
                diagnostics.increment("idle_watchdog_expiration_count")
                log(
                    "debug",
                    "Twitch IRC idle watchdog expired after "
                    f"{_IDLE_WATCHDOG_SECONDS}s; reconnecting.",
                )
                msg = "Twitch IRC connection became idle."
                raise ConnectionError(msg) from None

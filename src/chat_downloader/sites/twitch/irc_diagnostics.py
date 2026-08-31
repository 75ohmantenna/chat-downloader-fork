# SPDX-License-Identifier: MIT

"""Twitch IRC traffic classification and clean-run sample capture."""

from __future__ import annotations

import os

from chat_downloader.redaction import capture_debug_sample

_SUCCESSFUL_FRAME_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES"
_SUCCESSFUL_FRAME_CAPTURE_LIMIT = 3
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_CONTROL_FRAME_PREFIX_LIMIT = 128


def _control_command(frame_prefix: str) -> str | None:
    """Return an incoming IRC control command from a bounded frame prefix."""
    parts = frame_prefix.split()
    if not parts:
        return None
    command_index = 1 if parts[0].startswith(":") else 0
    if command_index >= len(parts):
        return None
    command = parts[command_index]
    return command if command in {"PING", "PONG"} else None


class _TwitchLiveDiagnostics:
    """Fixed-schema counters for one Twitch live IRC run."""

    def __init__(self) -> None:
        self.summary: dict[str, object] = {
            "connection_attempt_count": 0,
            "connection_success_count": 0,
            "connection_setup_failure_count": 0,
            "reconnect_count": 0,
            "server_reconnect_requested_count": 0,
            "received_irc_chunk_count": 0,
            "received_irc_frame_count": 0,
            "parsed_irc_message_count": 0,
            "receive_timeout_count": 0,
            "idle_watchdog_expiration_count": 0,
            "keepalive_ping_sent_count": 0,
            "keepalive_ping_received_count": 0,
            "keepalive_pong_sent_count": 0,
            "keepalive_pong_received_count": 0,
            "duplicate_message_suppressed_count": 0,
            "filtered_message_count": 0,
            "live_emitted_count": 0,
        }
        self.reset_transport_state()

    def reset_transport_state(self) -> None:
        """Discard one connection's partial frame without clearing counters."""
        self._frame_prefix = ""
        self._previous_character_was_cr = False

    def increment(self, name: str) -> None:
        """Increment a known integer counter without growing the schema."""
        value = self.summary.get(name)
        if isinstance(value, int):
            self.summary[name] = value + 1

    def record_received_data(self, data: str) -> int:
        """Count frames and return newly completed server PING commands."""
        completed_ping_count = 0
        self.increment("received_irc_chunk_count")
        for character in data:
            if character == "\n" and self._previous_character_was_cr:
                self.increment("received_irc_frame_count")
                control_command = _control_command(self._frame_prefix)
                if control_command == "PING":
                    self.increment("keepalive_ping_received_count")
                    completed_ping_count += 1
                elif control_command == "PONG":
                    self.increment("keepalive_pong_received_count")
                self._frame_prefix = ""
                self._previous_character_was_cr = False
                continue

            if len(self._frame_prefix) < _CONTROL_FRAME_PREFIX_LIMIT:
                self._frame_prefix += character
            self._previous_character_was_cr = character == "\r"
        return completed_ping_count


class _SuccessfulIrcFrameCapture:
    """Bound sanitized capture attempts across one Twitch live-chat run."""

    def __init__(self) -> None:
        self._enabled = (
            os.environ.get(_SUCCESSFUL_FRAME_CAPTURE_ENV, "").strip().lower()
            in _TRUTHY_ENV_VALUES
        )
        self._attempts = 0

    def capture(self, raw_frame: str) -> None:
        """Capture one of the first explicitly requested valid IRC frames."""
        if not self._enabled or self._attempts >= _SUCCESSFUL_FRAME_CAPTURE_LIMIT:
            return
        self._attempts += 1
        capture_debug_sample(
            "twitch-irc-frame",
            {"raw": raw_frame},
            sample_limit=_SUCCESSFUL_FRAME_CAPTURE_LIMIT,
        )


def _is_benign_unmatched_irc_buffer(readbuffer: str) -> bool:
    """Return True for unmatched IRC traffic that is expected and noisy."""
    lines = [line.strip() for line in readbuffer.splitlines() if line.strip()]
    if not lines:
        return True

    for line in lines:
        if _control_command(line) in {"PING", "PONG"}:
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

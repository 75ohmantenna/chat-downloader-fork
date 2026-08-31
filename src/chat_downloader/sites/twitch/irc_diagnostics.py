# SPDX-License-Identifier: MIT

"""Twitch live startup/IRC diagnostics and clean-run sample capture."""

from __future__ import annotations

import hashlib
import os
import re
from typing import TYPE_CHECKING

from chat_downloader.redaction import capture_debug_sample, sanitize_for_log

from .constants import (
    ACTION_TYPE_REMAPPING,
    MESSAGE_GROUPS,
    MESSAGE_TYPE_REMAPPING,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_SUCCESSFUL_FRAME_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES"
_SUCCESSFUL_FRAME_CAPTURE_LIMIT = 3
_EVENT_FRAME_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES"
_EVENT_FRAME_CAPTURE_LIMIT = 12
_EVENT_FRAME_CAPTURE_ATTEMPTS_PER_KEY = 2
_EVENT_FRAME_CAPTURE_GROUP = "twitch-irc-event-frames"
_EVENT_KEY_COMPONENT_LIMIT = 48
_EVENT_KEY_COMPONENT_RE = re.compile(r"[^a-z0-9]+")
_KNOWN_NORMALIZED_MESSAGE_TYPES = frozenset(
    message_type
    for message_types in MESSAGE_GROUPS.values()
    for message_type in message_types
)
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_CONTROL_FRAME_PREFIX_LIMIT = 128
_BENIGN_NUMERIC_COMMANDS = frozenset(
    {
        "001",  # welcome
        "002",  # host information
        "003",  # server creation time
        "004",  # server/version information
        "353",  # channel names
        "366",  # end of channel names
        "372",  # message of the day
        "375",  # start of message of the day
        "376",  # end of message of the day
    },
)
_BENIGN_CONTROL_COMMANDS = frozenset({"PING", "PONG", "JOIN", "PART", "CAP"}) | (
    _BENIGN_NUMERIC_COMMANDS
)


def _irc_command(frame_prefix: str) -> str | None:
    """Return an IRC command from a bounded frame prefix."""
    parts = frame_prefix.split()
    if not parts:
        return None
    command_index = 1 if parts[0].startswith("@") else 0
    if command_index < len(parts) and parts[command_index].startswith(":"):
        command_index += 1
    if command_index >= len(parts):
        return None
    return parts[command_index]


def _control_command(frame_prefix: str) -> str | None:
    """Return an incoming IRC keepalive command from a bounded frame prefix."""
    command = _irc_command(frame_prefix)
    return command if command in {"PING", "PONG"} else None


def _is_benign_control_frame(frame_prefix: str) -> bool:
    """Return whether a complete frame is recognized benign control traffic."""
    if frame_prefix.lstrip().startswith("@"):
        # Tagged frames can match MESSAGE_REGEX and contribute to parsed
        # message counts, so they are not part of this disjoint control count.
        return False
    command = _irc_command(frame_prefix)
    if command not in _BENIGN_CONTROL_COMMANDS:
        return False
    return _is_benign_unmatched_irc_buffer(frame_prefix)


class _TwitchLiveDiagnostics:
    """Fixed-schema counters for one Twitch live IRC run."""

    def __init__(self) -> None:
        self.summary: dict[str, object] = {
            "optional_metadata_degradation_count": 0,
            "connection_attempt_count": 0,
            "connection_success_count": 0,
            "connection_setup_failure_count": 0,
            "reconnect_count": 0,
            "server_reconnect_requested_count": 0,
            "received_irc_chunk_count": 0,
            "received_irc_frame_count": 0,
            "benign_irc_control_frame_count": 0,
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

    def record_optional_metadata_degradation(self) -> None:
        """Record one content-free recognized metadata degradation."""
        self.increment("optional_metadata_degradation_count")

    def record_received_data(self, data: str) -> int:
        """Count frames and return newly completed server PING commands."""
        completed_ping_count = 0
        self.increment("received_irc_chunk_count")
        for character in data:
            if character == "\n" and self._previous_character_was_cr:
                self.increment("received_irc_frame_count")
                control_command = _control_command(self._frame_prefix)
                if _is_benign_control_frame(self._frame_prefix):
                    self.increment("benign_irc_control_frame_count")
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


def _bounded_event_component(value: str) -> str:
    """Return a readable, collision-resistant component for a provider value."""
    normalized = _EVENT_KEY_COMPONENT_RE.sub("-", value.casefold()).strip("-")
    digest = hashlib.sha256(
        value.encode("utf-8"),
    ).hexdigest()[:12]
    prefix_limit = _EVENT_KEY_COMPONENT_LIMIT - len(digest) - 1
    prefix = normalized[:prefix_limit].rstrip("-") or "unknown"
    return f"{prefix}-{digest}"


def _action_event_component(raw_action: str) -> str:
    """Return a readable known action or opaque sanitized unknown identity."""
    if raw_action in ACTION_TYPE_REMAPPING:
        return _bounded_event_component(raw_action)

    sanitized_action = str(sanitize_for_log(raw_action))
    digest = hashlib.sha256(sanitized_action.encode("utf-8")).hexdigest()[:12]
    return f"unknown-{digest}"


def _irc_msg_id(raw_tags: str) -> tuple[bool, str]:
    """Return whether raw IRC tags contain ``msg-id`` and its exact value."""
    found = False
    msg_id = ""
    for raw_tag in raw_tags.split(";"):
        name, separator, value = raw_tag.partition("=")
        if name == "msg-id":
            found = True
            msg_id = value if separator else ""
    return found, msg_id


def _event_capture_key(
    parsed_item: Mapping[str, object],
    raw_action: str,
    raw_tags: str,
) -> str:
    """Classify an event from recognized raw IRC provenance."""
    has_msg_id, raw_msg_id = _irc_msg_id(raw_tags)
    if has_msg_id:
        normalized_message_type = MESSAGE_TYPE_REMAPPING.get(raw_msg_id)
        if normalized_message_type is not None:
            return f"message-{_bounded_event_component(normalized_message_type)}"
        return f"action-{_action_event_component(raw_action)}"

    message_type = parsed_item.get("message_type")
    if (
        raw_action in ACTION_TYPE_REMAPPING
        and isinstance(message_type, str)
        and message_type in _KNOWN_NORMALIZED_MESSAGE_TYPES
    ):
        return f"message-{_bounded_event_component(message_type)}"
    return f"action-{_action_event_component(raw_action)}"


class _EventDiverseIrcFrameCapture:
    """Capture one sanitized raw frame per bounded Twitch event key."""

    def __init__(self) -> None:
        self._enabled = (
            os.environ.get(_EVENT_FRAME_CAPTURE_ENV, "").strip().lower()
            in _TRUTHY_ENV_VALUES
        )
        self._captured_event_keys: set[str] = set()
        self._event_key_attempts: dict[str, int] = {}

    def capture(
        self,
        raw_frame: str,
        parsed_item: Mapping[str, object],
        raw_action: str,
        raw_tags: str,
    ) -> None:
        """Capture the first frame for one normalized, bounded event key."""
        if not self._enabled:
            return

        event_key = _event_capture_key(parsed_item, raw_action, raw_tags)
        if event_key in self._captured_event_keys:
            return

        attempts = self._event_key_attempts.get(event_key)
        if attempts is None:
            if len(self._event_key_attempts) >= _EVENT_FRAME_CAPTURE_LIMIT:
                return
            attempts = 0
        if attempts >= _EVENT_FRAME_CAPTURE_ATTEMPTS_PER_KEY:
            return
        self._event_key_attempts[event_key] = attempts + 1

        path = capture_debug_sample(
            f"twitch-irc-event-{event_key}",
            {"raw": raw_frame},
            sample_limit=1,
            sample_group=_EVENT_FRAME_CAPTURE_GROUP,
            group_limit=_EVENT_FRAME_CAPTURE_LIMIT,
        )
        if path is not None:
            self._captured_event_keys.add(event_key)


def _is_benign_unmatched_irc_buffer(readbuffer: str) -> bool:
    """Return True for unmatched IRC traffic that is expected and noisy."""
    lines = [line.strip() for line in readbuffer.splitlines() if line.strip()]
    if not lines:
        return True

    for line in lines:
        command = _irc_command(line)
        if command in {"PING", "PONG", "JOIN", "PART"}:
            continue
        if "tmi.twitch.tv" not in line:
            return False
        if command in _BENIGN_NUMERIC_COMMANDS:
            continue

        parts = line.split()
        if (
            len(parts) >= 4
            and command == "CAP"
            and parts[2] == "*"
            and parts[3] == "ACK"
        ):
            continue

        return False

    return True

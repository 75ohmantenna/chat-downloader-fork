# SPDX-License-Identifier: MIT

"""Kick/Pusher event dispatch.

A dispatch dictionary maps normalized message types (resolved from raw Pusher
``event`` names via ``EVENT_NAME_MAP``) to parser functions. Pusher protocol
control frames are recognized and ignored; unknown events are debug-logged by
name and can be captured through the sanitized opt-in sample mechanism before
being skipped so they can never crash normal chat logging.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import logger
from chat_downloader.errors import ParsingError
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.kick.constants import (
    EVENT_NAME_MAP,
    KICK_DEBUG_SAMPLE_LIMIT,
    KICK_UNKNOWN_EVENT_SAMPLE_LIMIT,
    MESSAGE_TYPE_REMAPPING,
    PUSHER_CONNECTION_ESTABLISHED,
    PUSHER_ERROR,
    PUSHER_PING,
    PUSHER_PONG,
    PUSHER_SUBSCRIPTION_SUCCEEDED,
)
from chat_downloader.sites.kick.errors import KickError
from chat_downloader.sites.kick.parsing.hosts import parse_stream_host_event
from chat_downloader.sites.kick.parsing.messages import parse_chat_message
from chat_downloader.sites.kick.parsing.moderation import (
    parse_chat_clear_event,
    parse_message_deleted_event,
    parse_user_banned_event,
    parse_user_unbanned_event,
)
from chat_downloader.sites.kick.parsing.pins import (
    parse_pinned_message_created_event,
    parse_pinned_message_deleted_event,
)
from chat_downloader.sites.kick.parsing.polls import (
    parse_poll_deleted_event,
    parse_poll_update_event,
)
from chat_downloader.sites.kick.parsing.subscriptions import (
    parse_gifted_subscriptions_event,
    parse_subscription_event,
)
from chat_downloader.utils.json_types import get_int, get_str

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

#: Pusher protocol control frames that are expected and silently ignored.
_KNOWN_CONTROL_EVENTS = frozenset(
    {
        PUSHER_CONNECTION_ESTABLISHED,
        PUSHER_SUBSCRIPTION_SUCCEEDED,
        PUSHER_PING,
        PUSHER_PONG,
    }
)

MALFORMED_EVENT_TYPE_DIAGNOSTIC_PREFIX = "malformed_event_type:"

#: Maps normalized message types to their parser functions.
_PARSER_DISPATCH: dict[str, Callable[[object], dict[str, Any] | None]] = {
    "text_message": parse_chat_message,
    "subscription": parse_subscription_event,
    "gifted_subscriptions": parse_gifted_subscriptions_event,
    "user_banned": parse_user_banned_event,
    "user_unbanned": parse_user_unbanned_event,
    "message_deleted": parse_message_deleted_event,
    "pinned_message": parse_pinned_message_created_event,
    "pinned_message_deleted": parse_pinned_message_deleted_event,
    "stream_host": parse_stream_host_event,
    "chat_clear": parse_chat_clear_event,
    "poll_update": parse_poll_update_event,
    "poll_deleted": parse_poll_deleted_event,
}


def _decode_event_data(data: object) -> object:
    """Decode a Pusher frame's ``data`` field.

    Kick double-encodes event payloads: ``data`` is a JSON *string* that must
    be parsed again. Some frames may already provide an object.

    Args:
        data: The raw ``data`` field of a Pusher frame.

    Returns:
        The decoded payload.

    Raises:
        ParsingError: If ``data`` is a string that is not valid JSON.
    """
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, ValueError) as error:
            msg = "Kick event 'data' was not valid JSON."
            raise ParsingError(msg) from error
    return data


def _record_diagnostic(
    callback: Callable[[str], None] | None,
    name: str,
) -> None:
    """Record one event-dispatch diagnostic when a callback is configured."""
    if callback is not None:
        callback(name)


def _normalize_compact_live_payload(
    message_type: str,
    payload: object,
    received_timestamp: int | None,
) -> object:
    """Expand observed compact live shapes into canonical parser inputs."""
    if (
        not isinstance(received_timestamp, int)
        or isinstance(received_timestamp, bool)
        or received_timestamp < 0
    ):
        return payload

    if message_type == "pinned_message_deleted" and payload == []:
        return {"id": f"kick-unpin:{received_timestamp}"}

    if message_type == "poll_deleted":
        normalized = dict(payload) if isinstance(payload, dict) else {}
        if normalized.get("id") is None:
            normalized["id"] = f"kick-poll-deleted:{received_timestamp}"
        return normalized

    if message_type == "poll_update" and isinstance(payload, dict):
        normalized = dict(payload)
        if normalized.get("id") is None:
            normalized["id"] = f"kick-poll-update:{received_timestamp}"
        return normalized

    if (
        message_type != "subscription"
        or not isinstance(payload, dict)
        or payload.get("id") is not None
        or "sender" in payload
        or "metadata" in payload
    ):
        return payload

    username = get_str(payload, "username").strip()
    months = get_int(payload, "months")
    chatroom_id = get_int(payload, "chatroom_id")
    if not username or months < 1 or chatroom_id < 1:
        return payload

    normalized = dict(payload)
    normalized.update(
        {
            "id": f"kick-subscription:{received_timestamp}",
            "sender": {"username": username},
            "metadata": {"subscription": {"months": months}},
        }
    )
    return normalized


def dispatch_event(
    frame: Mapping[str, object],
    *,
    record_diagnostic: Callable[[str], None] | None = None,
    received_timestamp: int | None = None,
) -> dict[str, Any] | None:
    """Dispatch one decoded Pusher frame to its handler.

    Args:
        frame: A decoded Pusher frame, expected to contain an ``event`` name
            and a ``data`` payload.
        record_diagnostic: Optional callback that increments a named live-run
            diagnostic counter.
        received_timestamp: UTC receive time in microseconds. Live-only compact
            payloads use it to construct namespaced fallback event IDs.

    Returns:
        A normalized chat message dictionary for a recognized event, or
        ``None`` for control frames, unknown/unsupported events, and
        unparsable payloads (which are skipped, not raised).

    Raises:
        KickError: If the frame is a ``pusher:error`` event, which indicates a
            subscription or protocol failure.
    """
    event_name = frame.get("event")

    # --- Pusher protocol errors -----------------------------------------------
    if event_name == PUSHER_ERROR:
        _record_diagnostic(record_diagnostic, "pusher_error_count")
        capture_debug_sample(
            "kick-pusher-error",
            {"raw": frame},
            sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        msg = "Kick Pusher returned an error event (subscription/protocol failure)."
        raise KickError(msg)

    # --- Pusher control frames (silently ignore) ------------------------------
    if event_name in _KNOWN_CONTROL_EVENTS:
        _record_diagnostic(record_diagnostic, "control_frame_count")
        logger.debug("Ignoring Kick Pusher control event: %s", event_name)
        return None

    # --- Resolve the Pusher event name to a normalized message type -----------
    if not isinstance(event_name, str):
        _record_diagnostic(record_diagnostic, "unsupported_event_count")
        capture_debug_sample(
            "kick-unknown-event",
            {"raw": frame, "reason": "missing or non-string event name"},
            sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        logger.debug("Kick Pusher frame has no event name; skipping.")
        return None
    message_type = EVENT_NAME_MAP.get(event_name)
    if message_type is None:
        _record_diagnostic(record_diagnostic, "unsupported_event_count")
        event_label = "kick-unknown-event-" + event_name.rsplit("\\", 1)[-1]
        capture_debug_sample(
            event_label,
            {"raw": frame, "event_name": event_name},
            sample_limit=KICK_UNKNOWN_EVENT_SAMPLE_LIMIT,
            sample_group="kick-unknown-event",
            group_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        logger.debug("Skipping unsupported Kick event: %s", event_name)
        return None

    # --- Look up the parser and run it ----------------------------------------
    parser = _PARSER_DISPATCH.get(message_type)
    if parser is None:  # pragma: no cover — programming error guard
        _record_diagnostic(record_diagnostic, "malformed_event_count")
        _record_diagnostic(
            record_diagnostic,
            MALFORMED_EVENT_TYPE_DIAGNOSTIC_PREFIX + message_type,
        )
        capture_debug_sample(
            "kick-malformed-event",
            {
                "raw": frame,
                "message_type": message_type,
                "reason": "no registered parser",
            },
            sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        logger.debug("No parser registered for message type: %s", message_type)
        return None

    try:
        payload = _decode_event_data(frame.get("data"))
        payload = _normalize_compact_live_payload(
            message_type,
            payload,
            received_timestamp,
        )
        if (
            message_type == "text_message"
            and isinstance(payload, dict)
            and str(payload.get("type")) not in MESSAGE_TYPE_REMAPPING
        ):
            _record_diagnostic(record_diagnostic, "unknown_message_type_count")
        message = parser(payload)
    except (
        ParsingError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        _record_diagnostic(record_diagnostic, "malformed_event_count")
        _record_diagnostic(
            record_diagnostic,
            MALFORMED_EVENT_TYPE_DIAGNOSTIC_PREFIX + message_type,
        )
        # A single malformed frame must never tear down the live download
        # loop (which only retries on ConnectionError); skip it instead.
        capture_debug_sample(
            "kick-malformed-event",
            {
                "raw": frame,
                "message_type": message_type,
                "error": str(error),
            },
            sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
        )
        logger.debug("Skipping malformed Kick %s: %s", message_type, error)
        return None
    else:
        if message is not None:
            _record_diagnostic(record_diagnostic, "parsed_event_count")
        return message

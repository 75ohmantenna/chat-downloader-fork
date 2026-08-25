# SPDX-License-Identifier: MIT

"""Kick/Pusher event dispatch.

A dispatch dictionary maps normalized message types (resolved from raw Pusher
``event`` names via ``EVENT_NAME_MAP``) to parser functions. Pusher protocol
control frames are recognized and ignored; unknown events are debug-logged
using their sanitized name only—never their payload body—and skipped so
they can never crash normal chat logging.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import logger
from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.constants import (
    EVENT_NAME_MAP,
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
from chat_downloader.sites.kick.parsing.subscriptions import (
    parse_gifted_subscriptions_event,
    parse_subscription_event,
)

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


def dispatch_event(frame: Mapping[str, object]) -> dict[str, Any] | None:
    """Dispatch one decoded Pusher frame to its handler.

    Args:
        frame: A decoded Pusher frame, expected to contain an ``event`` name
            and a ``data`` payload.

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
        msg = "Kick Pusher returned an error event (subscription/protocol failure)."
        raise KickError(msg)

    # --- Pusher control frames (silently ignore) ------------------------------
    if event_name in _KNOWN_CONTROL_EVENTS:
        logger.debug("Ignoring Kick Pusher control event: %s", event_name)
        return None

    # --- Resolve the Pusher event name to a normalized message type -----------
    if not isinstance(event_name, str):
        logger.debug("Kick Pusher frame has no event name; skipping.")
        return None
    message_type = EVENT_NAME_MAP.get(event_name)
    if (
        message_type is None
    ):  # pragma: no cover — unsupported events cannot hit this path in tests
        logger.debug("Skipping unsupported Kick event: %s", event_name)
        return None

    # --- Look up the parser and run it ----------------------------------------
    parser = _PARSER_DISPATCH.get(message_type)
    if parser is None:  # pragma: no cover — programming error guard
        logger.debug("No parser registered for message type: %s", message_type)
        return None

    try:
        payload = _decode_event_data(frame.get("data"))
        return parser(payload)
    except (
        ParsingError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:  # pragma: no cover — defensive
        # A single malformed frame must never tear down the live download
        # loop (which only retries on ConnectionError); skip it instead.
        logger.debug("Skipping malformed Kick %s: %s", message_type, error)
        return None

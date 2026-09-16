# SPDX-License-Identifier: MIT

r"""Normalize Kick stream-host events.

Handles ``App\Events\StreamHostEvent`` Pusher payloads.
"""

from __future__ import annotations

import contextlib
from typing import Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.common_fields import (
    _opt_str,
    _parse_author,
    _parse_timestamp,
)
from chat_downloader.utils.json_types import get_int, get_str


def normalize_compact_host(payload: object, received_timestamp: int) -> object:
    """Expand the observed ID-less live host shape without repairing bad IDs."""
    if (
        not isinstance(payload, dict)
        or payload.get("id") is not None
        or "sender" in payload
        or "metadata" in payload
    ):
        return payload
    username = get_str(payload, "host_username").strip()
    viewers = get_int(payload, "number_viewers", -1)
    if not username or viewers < 0 or get_int(payload, "chatroom_id") < 1:
        return payload
    normalized = dict(payload)
    normalized.update(
        {
            "id": f"kick-stream-host:{received_timestamp}",
            "sender": {"username": username},
            "content": get_str(payload, "optional_message"),
            "metadata": {"stream_host": payload},
        }
    )
    return normalized


def _extract_host_metadata(raw_meta: object) -> dict[str, Any]:
    """Extract structured stream-host metadata.

    Args:
        raw_meta: The ``metadata.stream_host`` sub-object.

    Returns:
        A dict with ``host_username``, ``number_viewers``, and
        ``optional_message`` when present.
    """
    host_meta: dict[str, Any] = {}
    if not isinstance(raw_meta, dict):  # pragma: no cover — defensive
        return host_meta

    host_username = _opt_str(raw_meta.get("host_username"))
    if host_username is not None:
        host_meta["host_username"] = host_username

    num_viewers = raw_meta.get("number_viewers")
    if num_viewers is not None:
        with contextlib.suppress(ValueError, TypeError):
            host_meta["number_viewers"] = int(num_viewers)

    optional_message = _opt_str(raw_meta.get("optional_message"))
    if optional_message is not None:
        host_meta["optional_message"] = optional_message

    return host_meta


def parse_stream_host_event(raw: object) -> dict[str, Any]:
    """Normalize a Kick stream-host event.

    Args:
        raw: The decoded ``StreamHostEvent`` payload.

    Returns:
        A normalized message dictionary with ``message_type`` set to
        ``"stream_host"``.

    Raises:
        ParsingError: If ``raw`` is not an object or lacks an ``id``.
    """
    if not isinstance(raw, dict):
        msg = "Kick stream-host event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if not message_id:
        msg = "Kick stream-host event payload was missing an id."
        raise ParsingError(msg)

    content = raw.get("content")
    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "stream_host",
        "message": content if isinstance(content, str) else "",
    }

    timestamp = _parse_timestamp(raw.get("created_at"))
    if timestamp is not None:
        info["timestamp"] = timestamp

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    raw_metadata = raw.get("metadata")
    if isinstance(raw_metadata, dict):
        host_meta = _extract_host_metadata(raw_metadata.get("stream_host"))
        if host_meta:
            info["metadata"] = host_meta

    return info

# SPDX-License-Identifier: MIT

r"""Normalize Kick stream-host events.

Handles ``App\Events\StreamHostEvent`` Pusher payloads.
"""

from __future__ import annotations

import contextlib
from typing import Any

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.messages import _opt_str, _parse_author
from chat_downloader.utils.time_utils import timestamp_to_microseconds


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
    if message_id is None:
        msg = "Kick stream-host event payload was missing an id."
        raise ParsingError(msg)

    content = raw.get("content")
    info: dict[str, Any] = {
        "message_id": message_id,
        "message_type": "stream_host",
        "message": content if isinstance(content, str) else "",
    }

    created_at = raw.get("created_at")
    if isinstance(created_at, str) and created_at:
        with contextlib.suppress(ValueError, TypeError):
            info["timestamp"] = timestamp_to_microseconds(created_at)

    author = _parse_author(raw.get("sender"))
    if author:
        info["author"] = author

    raw_metadata = raw.get("metadata")
    if isinstance(raw_metadata, dict):
        host_meta = _extract_host_metadata(raw_metadata.get("stream_host"))
        if host_meta:
            info["metadata"] = host_meta

    return info

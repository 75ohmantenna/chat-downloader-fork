# SPDX-License-Identifier: MIT

r"""Normalize Kick poll state events.

Handles ``App\Events\PollUpdateEvent`` and
``App\Events\PollDeleteEvent`` Pusher payloads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.common_fields import _opt_str
from chat_downloader.utils.json_types import get_dict, get_list, get_str

if TYPE_CHECKING:
    from collections.abc import Mapping

    from chat_downloader.utils.json_types import JSONDict, JSONList


def _parse_nonnegative_int(raw: Mapping[str, object], key: str) -> int | None:
    """Return a non-negative integer field without accepting booleans."""
    value = raw.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _parse_poll_option(raw: object) -> JSONDict:
    """Normalize one poll option while omitting malformed fields."""
    if not isinstance(raw, dict):
        return {}

    option: JSONDict = {}
    option_id = _parse_nonnegative_int(raw, "id")
    if option_id is not None:
        option["id"] = option_id
    label = get_str(raw, "label")
    if label:
        option["label"] = label
    votes = _parse_nonnegative_int(raw, "votes")
    if votes is not None:
        option["votes"] = votes
    return option


def _parse_poll_metadata(poll: JSONDict) -> JSONDict:
    """Normalize the changing countdown, options, votes, and viewer state."""
    metadata: JSONDict = {}
    for key in ("duration", "remaining", "result_display_duration"):
        value = _parse_nonnegative_int(poll, key)
        if value is not None:
            metadata[key] = value

    options: JSONList = []
    for raw_option in get_list(poll, "options"):
        option = _parse_poll_option(raw_option)
        if option:
            options.append(option)
    if options:
        metadata["options"] = options

    has_voted = poll.get("has_voted")
    if isinstance(has_voted, bool):
        metadata["has_voted"] = has_voted
    voted_option_id = _parse_nonnegative_int(poll, "voted_option_id")
    if voted_option_id is not None:
        metadata["voted_option_id"] = voted_option_id
    return metadata


def parse_poll_update_event(raw: object) -> JSONDict:
    """Normalize one poll-state update with its options and countdown."""
    if not isinstance(raw, dict):
        msg = "Kick poll-update event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick poll-update event payload was missing an id."
        raise ParsingError(msg)

    poll = get_dict(raw, "poll")
    if not poll:
        msg = "Kick poll-update event payload was missing poll data."
        raise ParsingError(msg)

    title = get_str(poll, "title")
    info: JSONDict = {
        "message_id": message_id,
        "message_type": "poll_update",
        "message": title,
    }
    metadata = _parse_poll_metadata(poll)
    if metadata:
        info["metadata"] = metadata
    return info


def parse_poll_deleted_event(raw: object) -> JSONDict:
    """Normalize a poll-deleted state signal, whose payload is not semantic."""
    if not isinstance(raw, dict):
        msg = "Kick poll-deleted event payload was not a JSON object."
        raise ParsingError(msg)

    message_id = _opt_str(raw.get("id"))
    if message_id is None:
        msg = "Kick poll-deleted event payload was missing an id."
        raise ParsingError(msg)

    return {
        "message_id": message_id,
        "message_type": "poll_deleted",
        "message": "",
    }

# SPDX-License-Identifier: MIT

"""Dispatcher for YouTube chat action processing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from chat_downloader.debugging import debug_log
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _KNOWN_ADD_ACTION_TYPES,
    _KNOWN_ADD_BANNER_TYPES,
    _KNOWN_ADD_TICKER_TYPES,
    _KNOWN_IGNORE_ACTION_TYPES,
    _KNOWN_INTERACTIVITY_WIDGET_ACTION_TYPES,
    _KNOWN_POLL_ACTION_TYPES,
    _KNOWN_REMOVE_ACTION_TYPES,
    _KNOWN_REMOVE_BANNER_TYPES,
    _KNOWN_REPLACE_ACTION_TYPES,
    _KNOWN_TOOLTIP_ACTION_TYPES,
)
from chat_downloader.utils.dict_utils import try_get_first_key
from chat_downloader.utils.json_types import JSONDict, get_dict, get_list, get_str
from chat_downloader.utils.string_utils import camel_case_split, remove_suffixes

from .actions_handlers_parser import (
    _handle_add_banner_action,
    _handle_interactivity_widget_action,
    _handle_item_action,
    _handle_poll_action,
    _handle_remove_action,
    _handle_remove_banner_action,
    _handle_replace_action,
    _handle_tooltip_action,
)

# Known item action types (combination of add and ticker types)
_KNOWN_ITEM_ACTION_TYPES = {
    **_KNOWN_ADD_ACTION_TYPES,
    **_KNOWN_ADD_TICKER_TYPES,
}

_ActionHandler = Callable[
    [JSONDict, str, dict[str, Any], float],
    tuple[dict[str, Any], dict[str, Any], str | None, str],
]

_ACTION_HANDLERS: tuple[tuple[dict[str, list[str]], _ActionHandler], ...] = (
    (_KNOWN_ITEM_ACTION_TYPES, _handle_item_action),
    (
        _KNOWN_INTERACTIVITY_WIDGET_ACTION_TYPES,
        _handle_interactivity_widget_action,
    ),
    (_KNOWN_REMOVE_ACTION_TYPES, _handle_remove_action),
    (_KNOWN_REPLACE_ACTION_TYPES, _handle_replace_action),
    (_KNOWN_TOOLTIP_ACTION_TYPES, _handle_tooltip_action),
    (_KNOWN_ADD_BANNER_TYPES, _handle_add_banner_action),
    (_KNOWN_REMOVE_BANNER_TYPES, _handle_remove_banner_action),
    (_KNOWN_POLL_ACTION_TYPES, _handle_poll_action),
)


@dataclass(frozen=True)
class ProcessedAction:
    """Typed result of processing a single YouTube chat action."""

    parsed_data: dict[str, Any]
    original_item: dict[str, Any]
    message_type: str | None
    action_type: str


def _make_processed_action(
    t: tuple[dict[str, Any], dict[str, Any], str | None, str],
) -> ProcessedAction:
    parsed_data, original_item, message_type, action_type = t
    return ProcessedAction(
        parsed_data=parsed_data,
        original_item=original_item,
        message_type=message_type,
        action_type=action_type,
    )


def process_action(
    action: JSONDict,
    offset: float = 0,
) -> ProcessedAction | None:
    """Process a YouTube chat action and return parsed message data.

    :param action: The action object to process
    :type action: dict
    :param offset: Time offset in milliseconds for replay chat
    :type offset: int
    :return: ProcessedAction with named fields, or None if action is ignored
    :rtype: ProcessedAction or None
    """
    data: dict[str, Any] = {}

    # Handle replay chat item actions (need to re-base time)
    replay_chat_item_action = get_dict(action, "replayChatItemAction")
    if replay_chat_item_action:
        offset_time = get_str(replay_chat_item_action, "videoOffsetTimeMsec")
        if offset_time:
            data["time_in_seconds"] = float(offset_time) / 1000
        actions_list = get_list(replay_chat_item_action, "actions")
        action = cast("JSONDict", actions_list[0]) if actions_list else action

    # Remove tracking params and get action type
    action.pop("clickTrackingParams", None)
    original_action_type = try_get_first_key(action)

    if not original_action_type:
        return None

    data["action_type"] = camel_case_split(
        remove_suffixes(original_action_type, ("Action", "Command")),
    )

    for type_set, handler in _ACTION_HANDLERS:
        if original_action_type in type_set:
            return _make_processed_action(
                handler(action, original_action_type, data, offset)
            )
    if original_action_type in _KNOWN_IGNORE_ACTION_TYPES:
        return None
    capture_debug_sample(
        f"youtube-unknown-action-{original_action_type}",
        {"action": action, "parsed_data": data},
    )
    debug_log(f"Unknown action: {original_action_type}", action, data)
    return None

# SPDX-License-Identifier: MIT

"""Dispatcher for YouTube chat action processing."""

from typing import Any

from chat_downloader.debugging import capture_debug_sample, debug_log
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _KNOWN_ADD_ACTION_TYPES,
    _KNOWN_ADD_BANNER_TYPES,
    _KNOWN_ADD_TICKER_TYPES,
    _KNOWN_IGNORE_ACTION_TYPES,
    _KNOWN_POLL_ACTION_TYPES,
    _KNOWN_REMOVE_ACTION_TYPES,
    _KNOWN_REMOVE_BANNER_TYPES,
    _KNOWN_REPLACE_ACTION_TYPES,
    _KNOWN_TOOLTIP_ACTION_TYPES,
)
from chat_downloader.utils.dict_utils import try_get_first_key
from chat_downloader.utils.string_utils import camel_case_split, remove_suffixes

from .actions_handlers import (
    _handle_add_banner_action,
    _handle_item_action,
    _handle_poll_action,
    _handle_remove_action,
    _handle_remove_banner_action,
    _handle_replace_action,
    _handle_tooltip_action,
)

# Known item action types (combination of add and ticker types)
_KNOWN_ITEM_ACTION_TYPES = _KNOWN_ADD_ACTION_TYPES | _KNOWN_ADD_TICKER_TYPES


def process_action(
    action: dict[str, Any],
    offset: float = 0,
) -> tuple[dict[str, Any], dict[str, Any], str | None, str] | None:
    """Process a YouTube chat action and return parsed message data.

    :param action: The action object to process
    :type action: dict
    :param offset: Time offset in milliseconds for replay chat
    :type offset: int
    :return: Tuple of (parsed_data, original_item, original_message_type,
        original_action_type) or None if action should be ignored
    :rtype: tuple or None
    """
    data: dict[str, Any] = {}

    # Handle replay chat item actions (need to re-base time)
    replay_chat_item_action = action.get("replayChatItemAction")
    if replay_chat_item_action:
        offset_time = replay_chat_item_action.get("videoOffsetTimeMsec")
        if offset_time:
            data["time_in_seconds"] = float(offset_time) / 1000
        action = replay_chat_item_action["actions"][0]

    # Remove tracking params and get action type
    action.pop("clickTrackingParams", None)
    original_action_type = try_get_first_key(action)

    if not original_action_type:
        return None

    data["action_type"] = camel_case_split(
        remove_suffixes(original_action_type, ("Action", "Command")),
    )

    # Route to appropriate handler
    match original_action_type:
        case _ if original_action_type in _KNOWN_ITEM_ACTION_TYPES:
            return _handle_item_action(
                action, original_action_type, data, offset
            )
        case _ if original_action_type in _KNOWN_REMOVE_ACTION_TYPES:
            return _handle_remove_action(
                action, original_action_type, data, offset
            )
        case _ if original_action_type in _KNOWN_REPLACE_ACTION_TYPES:
            return _handle_replace_action(
                action, original_action_type, data, offset
            )
        case _ if original_action_type in _KNOWN_TOOLTIP_ACTION_TYPES:
            return _handle_tooltip_action(
                action, original_action_type, data, offset
            )
        case _ if original_action_type in _KNOWN_ADD_BANNER_TYPES:
            return _handle_add_banner_action(
                action, original_action_type, data, offset
            )
        case _ if original_action_type in _KNOWN_REMOVE_BANNER_TYPES:
            return _handle_remove_banner_action(
                action,
                original_action_type,
                data,
                offset,
            )
        case _ if original_action_type in _KNOWN_POLL_ACTION_TYPES:
            return _handle_poll_action(
                action,
                original_action_type,
                data,
                offset,
            )
        case _ if original_action_type in _KNOWN_IGNORE_ACTION_TYPES:
            return None  # Ignore these actions
        case _:
            # Unknown action type
            capture_debug_sample(
                f"youtube-unknown-action-{original_action_type}",
                {"action": action, "parsed_data": data},
            )
            debug_log(f"Unknown action: {original_action_type}", action, data)
            return None

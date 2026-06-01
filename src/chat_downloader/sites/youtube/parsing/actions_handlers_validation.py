# SPDX-License-Identifier: MIT

"""Validation and normalization for parsed YouTube chat actions."""

from typing import Any

from chat_downloader.debugging import capture_debug_sample, debug_log
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _KNOWN_ACTION_TYPES,
)
from chat_downloader.sites.youtube.constants_actions_messages_list import (
    _KNOWN_IGNORE_MESSAGE_TYPES,
)
from chat_downloader.sites.youtube.constants_message import (
    _KNOWN_KEYS,
)
from chat_downloader.utils.string_utils import (
    camel_case_split,
    remove_prefixes,
    remove_suffixes,
)

_MODE_ICON_TO_TYPE: dict[str, str] = {
    "SLOW_MODE": "slow_mode_message",
    "MEMBERS_ONLY": "members_only_mode_message",
    "SUBSCRIBERS_ONLY": "subscribers_only_mode_message",
    "EMOTE_ONLY": "emote_only_mode_message",
}


def validate_and_finalize_message(
    data: dict[str, Any],
    original_item: dict[str, Any],
    original_message_type: str | None,
    original_action_type: str,
) -> dict[str, Any] | None:
    """Validate parsed message and add message_type field."""
    if not original_message_type:
        debug_log("No message type", f"Action type: {original_action_type}")
        return None

    test_for_missing_keys = original_item.get(original_message_type, {}).keys()
    missing_keys = test_for_missing_keys - _KNOWN_KEYS

    if not data:
        capture_debug_sample(
            f"youtube-empty-action-parse-{original_action_type}",
            {
                "original_item": original_item,
                "original_action_type": original_action_type,
            },
        )
        debug_log(
            f"Parse of action returned empty results: {original_action_type}",
            original_item,
        )

    if missing_keys:
        capture_debug_sample(
            f"youtube-missing-keys-{original_message_type}",
            {
                "original_item": original_item,
                "original_action_type": original_action_type,
                "original_message_type": original_message_type,
                "missing_keys": sorted(missing_keys),
            },
        )
        debug_log(
            f"Missing keys found: {missing_keys}",
            f"Message type: {original_message_type}",
        )

    new_index = remove_prefixes(original_message_type, "liveChat")
    new_index = remove_suffixes(new_index, "Renderer")
    data["message_type"] = camel_case_split(new_index)

    if original_message_type == "liveChatModeChangeMessageRenderer":
        icon = data.get("icon")
        if icon in _MODE_ICON_TO_TYPE:
            data["message_type"] = _MODE_ICON_TO_TYPE[icon]

    if original_message_type in _KNOWN_IGNORE_MESSAGE_TYPES:
        return None

    if original_message_type not in _KNOWN_ACTION_TYPES.get(
        original_action_type,
        set(),
    ):
        capture_debug_sample(
            f"youtube-unknown-message-type-{original_message_type}",
            {
                "data": data,
                "original_item": original_item,
                "original_action_type": original_action_type,
                "original_message_type": original_message_type,
            },
        )
        debug_log(
            f'Unknown message type "{original_message_type}" '
            f'for action "{original_action_type}"',
        )

    return data

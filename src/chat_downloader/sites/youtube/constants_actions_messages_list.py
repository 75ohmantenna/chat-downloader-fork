# SPDX-License-Identifier: MIT

"""Derived action lists for YouTube chat messages."""

from .constants_actions_messages_core import _KNOWN_ACTION_TYPES

_KNOWN_IGNORE_MESSAGE_TYPES = ["liveChatPlaceholderItemRenderer"]
_KNOWN_MESSAGE_TYPES = []
for _action_types in _KNOWN_ACTION_TYPES.values():
    _KNOWN_MESSAGE_TYPES += _action_types

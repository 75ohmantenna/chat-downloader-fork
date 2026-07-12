# SPDX-License-Identifier: MIT

"""Stateless helpers shared by site downloaders.

These utilities hold no runtime state and do not depend on
``BaseChatDownloader``; keep them free of session or instance concerns so any
site module can import them without pulling in the base downloader.
"""

from __future__ import annotations

from typing import Any

from chat_downloader.errors import InvalidParameter

from .remap import Remapper


def check_for_invalid_types(
    messages_types_to_add: list[str],
    allowed_message_types: list[str],
) -> None:
    """Raise if any requested message type is not allowed.

    Args:
        messages_types_to_add: Message type names requested by the caller.
        allowed_message_types: Valid type names for this site.

    Raises:
        InvalidParameter: If any requested type is not in the allowed set.
    """
    invalid_types = set(messages_types_to_add) - set(allowed_message_types)
    if invalid_types:
        msg = f"Invalid types specified: {invalid_types}"
        raise InvalidParameter(msg)


def get_mapped_keys(remapping: dict[str, Any]) -> set[Any]:
    """Return the set of destination keys produced by ``remapping``.

    Args:
        remapping: Dict mapping source keys to ``Remapper`` objects or
            plain destination key strings.

    Returns:
        Set of all destination key values.
    """
    mapped_keys = set()
    for raw in remapping.values():
        mapped_keys.add(raw.new_key if isinstance(raw, Remapper) else raw)
    return mapped_keys

# SPDX-License-Identifier: MIT

"""Invariant tests for YouTube remapping and routing tables.

These tests assert structural properties that a careless drift edit breaks, so
failures appear in CI instead of emitting a runtime debug_log that nobody sees.
"""

from __future__ import annotations

from chat_downloader.sites.youtube import constants_message as cm
from chat_downloader.sites.youtube.constants_actions_messages_core import (
    _KNOWN_ACTION_TYPES,
)
from chat_downloader.sites.youtube.constants_actions_messages_list import (
    _KNOWN_IGNORE_MESSAGE_TYPES,
)
from chat_downloader.utils.string_utils import (
    camel_case_split,
    remove_prefixes,
    remove_suffixes,
)

# Renderer strings that are set synthetically by handler code (not read from the
# API payload renderer key) or that are intentionally omitted from the filter
# group table.
_EXCLUDED_FROM_GROUP_CHECK: frozenset[str] = frozenset(
    {
        # REMOVE action types: handlers set message_type to "banUser" /
        # "deletedMessage" directly in _handle_remove_action.
        "banUser",
        "deletedMessage",
        # REMOVE_BANNER: handler sets "removeBanner" directly; banner removal
        # is not tracked as a filter group.
        "removeBanner",
        # TOOLTIP: tooltips are UI overlays, intentionally not in filter groups.
        "tooltipRenderer",
        # POLL: handler sets "pollRenderer" / "pollClosedEvent" directly.
        "pollRenderer",
        "pollClosedEvent",
    }
)

# Renderers whose message_type is overridden in validate_and_finalize_message
# rather than being derived by the standard camel_case transform.
_MESSAGE_TYPE_OVERRIDES: dict[str, str] = {
    "liveChatProductItemRenderer": "purchased_product_message",
}


def _renderer_to_message_type(renderer: str) -> str:
    """Apply the same transform that validate_and_finalize_message uses."""
    name = remove_prefixes(renderer, "liveChat")
    name = remove_suffixes(name, "Renderer")
    return camel_case_split(name)


def test_remapping_contributor_sets_are_disjoint() -> None:
    """No key may appear in both build_remapping() and _KEYS_TO_IGNORE."""
    remap = set(cm.build_remapping())
    colour = set(cm._COLOUR_KEYS)
    ignore = set(cm._KEYS_TO_IGNORE)
    assert remap & ignore == set(), (
        f"in both remap and ignore: {remap & ignore}"
    )
    assert colour & ignore == set(), (
        f"in both colour and ignore: {colour & ignore}"
    )
    assert remap & colour == set(), (
        f"in both remap and colour: {remap & colour}"
    )


def test_known_keys_derived_from_build_remapping() -> None:
    """known_keys() must exactly equal the derived union.

    Guards against any manual entry diverging from build_remapping() keys
    and the three supplemental lists.
    """
    expected = frozenset(cm.build_remapping()) | frozenset(
        cm._COLOUR_KEYS + cm._STICKER_KEYS + cm._KEYS_TO_IGNORE,
    )
    assert cm.known_keys() == expected


def test_every_routed_renderer_has_a_message_group() -> None:
    """Every routed renderer must produce a filterable message_type.

    Each renderer in _KNOWN_ACTION_TYPES must derive a message_type that
    exists in _MESSAGE_TYPES so it can be reached by the group filter.
    """
    missing = []
    for renderers in _KNOWN_ACTION_TYPES.values():
        for renderer in renderers:
            if (
                renderer in _EXCLUDED_FROM_GROUP_CHECK
                or renderer in _KNOWN_IGNORE_MESSAGE_TYPES
            ):
                continue
            mt = _MESSAGE_TYPE_OVERRIDES.get(
                renderer, _renderer_to_message_type(renderer)
            )
            if mt not in cm._MESSAGE_TYPES:
                missing.append((renderer, mt))
    assert not missing, "Routed renderers with no message group:\n" + "\n".join(
        f"  {r!r} → {mt!r}" for r, mt in missing
    )

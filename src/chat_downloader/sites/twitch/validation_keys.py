# SPDX-License-Identifier: MIT

"""Known-key set builders for Twitch payload validation."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from chat_downloader.sites.common import get_mapped_keys
from chat_downloader.utils.json_types import get_dict, get_list, get_str

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict, JSONList

OPTIONAL_TWITCH_PASSTHROUGH_KEYS = {
    "animation_id",
    "msg_param_gift_theme",
    "msg_param_goal_contribution_type",
    "msg_param_goal_current_contributions",
    "msg_param_goal_description",
    "msg_param_goal_target_contributions",
    "msg_param_goal_user_contributions",
}

_VOD_EDGE_KEYS = frozenset({"__typename", "cursor", "node"})
_VOD_NODE_KEYS = frozenset(
    {
        "__typename",
        "id",
        "createdAt",
        "commenter",
        "contentOffsetSeconds",
        "message",
    }
)
_VOD_COMMENTER_KEYS = frozenset(
    {
        "__typename",
        "id",
        "name",
        "login",
        "displayName",
        "profileImageURL",
        "primaryColorHex",
    }
)
_VOD_MESSAGE_KEYS = frozenset({"__typename", "userColor", "userBadges", "fragments"})
_VOD_BADGE_KEYS = frozenset({"__typename", "setID", "version"})
_VOD_FRAGMENT_KEYS = frozenset({"__typename", "text", "emote"})
_VOD_EMOTE_KEYS = frozenset({"__typename", "emoteID", "id"})
_VOD_EDGE_TYPENAMES = frozenset({"VideoCommentEdge"})
_VOD_NODE_TYPENAMES = frozenset({"Comment", "VideoComment"})


def _find_unexpected_keys(
    payload: JSONDict,
    expected: frozenset[str],
    path: str,
) -> list[str]:
    """Return qualified keys not present in one GraphQL object schema."""
    return [f"{path}.{key}" for key in sorted(payload.keys() - expected)]


def _find_unexpected_typename(
    payload: JSONDict,
    expected: frozenset[str],
    path: str,
) -> list[str]:
    """Return a qualified typename marker when a discriminator has drifted."""
    raw_typename = payload.get("__typename")
    if raw_typename is None:
        return []
    typename = get_str(payload, "__typename")
    if typename in expected:
        return []
    return [f"{path}.__typename={raw_typename!r}"]


def _get_shape_dict(
    payload: JSONDict,
    key: str,
    path: str,
    unexpected: list[str],
) -> JSONDict | None:
    """Narrow an optional object field and record incompatible containers."""
    raw_value = payload.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, dict):
        unexpected.append(f"{path}.{key} (expected object or null)")
        return None
    return get_dict(payload, key)


def _get_shape_list(
    payload: JSONDict,
    key: str,
    path: str,
    unexpected: list[str],
) -> JSONList:
    """Narrow an optional list field and record incompatible containers."""
    raw_value = payload.get(key)
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        unexpected.append(f"{path}.{key} (expected list or null)")
        return []
    return get_list(payload, key)


def _find_unexpected_list_item(
    item: object,
    expected: frozenset[str],
    path: str,
) -> tuple[JSONDict | None, list[str]]:
    """Validate one GraphQL object-list entry and return its narrowed value."""
    if not isinstance(item, dict):
        return None, [f"{path} (expected object)"]
    return item, _find_unexpected_keys(item, expected, path)


def find_unexpected_vod_edge_paths(edge: JSONDict) -> list[str]:
    """Return raw GraphQL VOD edge paths that differ from the parsed schema."""
    unexpected = _find_unexpected_keys(edge, _VOD_EDGE_KEYS, "edge")
    unexpected.extend(
        _find_unexpected_typename(
            edge,
            _VOD_EDGE_TYPENAMES,
            "edge",
        )
    )

    node = _get_shape_dict(edge, "node", "edge", unexpected)
    if node is None:
        return sorted(unexpected)
    unexpected.extend(_find_unexpected_keys(node, _VOD_NODE_KEYS, "edge.node"))
    unexpected.extend(
        _find_unexpected_typename(
            node,
            _VOD_NODE_TYPENAMES,
            "edge.node",
        )
    )

    commenter = _get_shape_dict(node, "commenter", "edge.node", unexpected)
    if commenter is not None:
        unexpected.extend(
            _find_unexpected_keys(
                commenter,
                _VOD_COMMENTER_KEYS,
                "edge.node.commenter",
            )
        )

    message = _get_shape_dict(node, "message", "edge.node", unexpected)
    if message is None:
        return sorted(unexpected)
    unexpected.extend(
        _find_unexpected_keys(message, _VOD_MESSAGE_KEYS, "edge.node.message")
    )

    badges = _get_shape_list(
        message,
        "userBadges",
        "edge.node.message",
        unexpected,
    )
    for index, raw_badge in enumerate(badges):
        path = f"edge.node.message.userBadges[{index}]"
        _, badge_drift = _find_unexpected_list_item(
            raw_badge,
            _VOD_BADGE_KEYS,
            path,
        )
        unexpected.extend(badge_drift)

    fragments = _get_shape_list(
        message,
        "fragments",
        "edge.node.message",
        unexpected,
    )
    for index, raw_fragment in enumerate(fragments):
        path = f"edge.node.message.fragments[{index}]"
        fragment, fragment_drift = _find_unexpected_list_item(
            raw_fragment,
            _VOD_FRAGMENT_KEYS,
            path,
        )
        unexpected.extend(fragment_drift)
        if fragment is None:
            continue
        emote = _get_shape_dict(fragment, "emote", path, unexpected)
        if emote is not None:
            unexpected.extend(
                _find_unexpected_keys(emote, _VOD_EMOTE_KEYS, f"{path}.emote")
            )

    return sorted(unexpected)


@cache
def build_known_comment_keys() -> set[str]:
    """Build set of known comment keys."""
    from .remappings import (
        build_comment_remapping,
        build_message_param_remapping,
    )

    comment_remapping = build_comment_remapping()
    message_param_remapping = build_message_param_remapping()

    known_keys = {
        "message",
        "time_in_seconds",
        "message_id",
        "time_text",
        "author",
        "timestamp",
        "message_type",
        "emotes",
    }
    known_keys.update(
        get_mapped_keys(
            {**comment_remapping, **message_param_remapping},
        ),
    )
    known_keys.update(OPTIONAL_TWITCH_PASSTHROUGH_KEYS)
    return known_keys


@cache
def build_known_irc_keys() -> set[str]:
    """Build set of known IRC keys."""
    from .remappings import build_irc_remapping

    irc_remapping = build_irc_remapping()

    known_keys = {
        "banned_user",
        "ban_type",
        "seconds_to_wait",
        "minutes_to_follow_before_chatting",
        "action_type",
        "author",
        "is_shared_chat_message",
        "in_reply_to",
        "message",
        "shared_chat_effective_source_channel_id",
        "shared_chat_is_cross_channel",
    }
    known_keys.update(get_mapped_keys(irc_remapping))
    known_keys.update(OPTIONAL_TWITCH_PASSTHROUGH_KEYS)
    return known_keys

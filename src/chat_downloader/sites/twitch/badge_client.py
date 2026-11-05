# SPDX-License-Identifier: MIT

"""Twitch badge retrieval, fallback, normalization, and cache updates."""

from __future__ import annotations

import base64
from json import JSONDecodeError
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import ChatDownloaderError, ParsingError
from chat_downloader.utils.json_types import get_str

from .graphql_client import _PersistedQueryUnavailable
from .types import _BADGE_CHANNEL_LIMIT

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict, JSONList

    from ._protocols import _DownloadGQL, _SessionPost


class _BadgeShapeError(ParsingError):
    """Raised when a Twitch badge response has drifted containers."""


def _warn_badge_failure(channel: str, source: str, error: Exception) -> None:
    """Report a failed collection without discarding its previous metadata."""
    log(
        "warning",
        f"Failed to retrieve {source} badge information for channel "
        f"'{channel}': {type(error).__name__}: {error}. Continuing "
        "without badges from this refresh; retaining cached metadata when available.",
    )


def _store_response_collections(
    data: JSONDict,
    channel: str,
    *,
    is_channel_response: bool,
    forced_channel_id: str | None,
    channel_id: str | None,
    badge_info: dict[tuple[str, str], JSONDict],
    subscriber_badge_info: dict[str, dict[tuple[str, str], JSONDict]],
    refreshed_globals: dict[tuple[str, str], JSONDict],
) -> bool:
    """Keep root-global and broadcast collection provenance and failures separate."""
    global_updated = False
    for global_collection in (True, False) if is_channel_response else (True,):
        if global_collection and is_channel_response and "badges" not in data:
            continue
        try:
            badges = (
                _get_badge_list(data, "badges")
                if global_collection
                else _get_user_badges(data)
            )
            target_globals = (
                {**badge_info, **refreshed_globals} if global_collection else badge_info
            )
            forced_id = (
                ""
                if global_collection and forced_channel_id is not None
                else forced_channel_id
            )
            _store_badges(
                badges,
                forced_id,
                target_globals,
                subscriber_badge_info,
                channel_id=None if global_collection else channel_id,
                replace_global=global_collection,
            )
            if global_collection:
                refreshed_globals.update(target_globals)
                global_updated = True
        except (
            RequestException,
            JSONDecodeError,
            KeyError,
            ValueError,
            ChatDownloaderError,
        ) as error:
            _warn_badge_failure(
                channel, "global" if global_collection else "channel", error
            )
    return global_updated


def update_badge_info(
    session_post: _SessionPost,
    channel: str,
    download_gql_func: _DownloadGQL,
    badge_info: dict[tuple[str, str], JSONDict],
    subscriber_badge_info: dict[str, dict[tuple[str, str], JSONDict]],
    channel_id: str | None = None,
    client_id: str | None = None,
) -> None:
    """Update badge caches while isolating channel and global failures."""
    refreshed_globals: dict[tuple[str, str], JSONDict] = {}
    global_updated = False
    for source_name in ("channel", "global"):
        try:
            if source_name == "channel":
                data, forced_channel_id = _download_channel_badges(
                    session_post, channel, channel_id, download_gql_func, client_id
                )
            else:
                data, forced_channel_id = _download_global_badges(
                    session_post, download_gql_func, client_id
                )
        except (
            RequestException,
            JSONDecodeError,
            KeyError,
            ValueError,
            ChatDownloaderError,
        ) as error:
            _warn_badge_failure(channel, source_name, error)
            continue
        if _store_response_collections(
            data,
            channel,
            is_channel_response=source_name == "channel",
            forced_channel_id=forced_channel_id,
            channel_id=channel_id,
            badge_info=badge_info,
            subscriber_badge_info=subscriber_badge_info,
            refreshed_globals=refreshed_globals,
        ):
            global_updated = True
    if global_updated:
        badge_info.clear()
        badge_info.update(refreshed_globals)


def _download_channel_badges(
    session_post: _SessionPost,
    channel: str,
    channel_id: str | None,
    download_gql_func: _DownloadGQL,
    client_id: str | None,
) -> tuple[JSONDict, str | None]:
    """Fetch channel badges, falling back to the current ID-based operation."""
    query: JSONList = [
        {
            "operationName": "ChatList_Badges",
            "variables": {"channelLogin": channel},
        }
    ]
    try:
        data = _get_response_data(
            download_gql_func(session_post, query, client_id=client_id)
        )
    except _PersistedQueryUnavailable:
        if not channel_id:
            raise
        query = [
            {
                "operationName": "BroadcastBadges",
                "variables": {"userID": channel_id},
            }
        ]
        data = _get_response_data(
            download_gql_func(session_post, query, client_id=client_id)
        )
        return data, channel_id
    return data, None


def _download_global_badges(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    client_id: str | None,
) -> tuple[JSONDict, str | None]:
    """Fetch global badges, falling back to the current mobile hash."""
    try:
        data = _get_response_data(
            download_gql_func(
                session_post,
                [{"operationName": "GlobalBadges"}],
                client_id=client_id,
            )
        )
    except _PersistedQueryUnavailable:
        data = _get_response_data(
            download_gql_func(
                session_post,
                [{"operationName": "GlobalBadgesMobile"}],
                client_id=client_id,
            )
        )
        return data, ""
    return data, None


def _get_response_data(response: JSONList) -> JSONDict:
    """Return the first GraphQL data object or reject a drifted container."""
    if not response or not isinstance(response[0], dict):
        msg = "badge response does not contain an operation object"
        raise _BadgeShapeError(msg)
    raw_data = response[0].get("data")
    if not isinstance(raw_data, dict):
        msg = "badge response data is not an object"
        raise _BadgeShapeError(msg)
    return raw_data


def _get_badge_list(container: JSONDict, key: str) -> JSONList:
    """Return a required badge list, distinguishing absent data from empty."""
    raw_badges = container.get(key)
    if not isinstance(raw_badges, list):
        msg = f"badge response {key} is not a list"
        raise _BadgeShapeError(msg)
    return raw_badges


def _get_user_badges(data: JSONDict) -> JSONList:
    """Return required nested broadcast badges with container validation."""
    raw_user = data.get("user")
    if not isinstance(raw_user, dict):
        msg = "badge response user is not an object"
        raise _BadgeShapeError(msg)
    return _get_badge_list(raw_user, "broadcastBadges")


def _badge_identity(
    raw_badge: JSONDict, forced_channel_id: str | None
) -> tuple[str, str, str] | None:
    """Read a legacy encoded identity or the current mobile identity."""
    if forced_channel_id is None:
        try:
            set_id, version, channel_id, *_ = (
                base64.b64decode(get_str(raw_badge, "id")).decode().strip().split(";")
            )
        except (ValueError, KeyError) as badge_error:
            log(
                "debug",
                f"Skipping malformed badge (id={raw_badge.get('id')!r}): {badge_error}",
            )
            return None
    else:
        set_id = get_str(raw_badge, "setID")
        version = get_str(raw_badge, "version")
        channel_id = forced_channel_id
    if not set_id.strip() or not version.strip():
        log("debug", "Skipping malformed badge without set/version")
        return None
    return set_id, version, channel_id


def _store_badges(
    badges: JSONList,
    forced_channel_id: str | None,
    badge_info: dict[tuple[str, str], JSONDict],
    subscriber_badge_info: dict[str, dict[tuple[str, str], JSONDict]],
    *,
    channel_id: str | None,
    replace_global: bool,
) -> None:
    """Stage a source refresh before replacing its successful collections."""
    global_badges: dict[tuple[str, str], JSONDict] = {}
    channel_badges: dict[str, dict[tuple[str, str], JSONDict]] = (
        {channel_id: {}} if channel_id else {}
    )
    for raw_badge in badges:
        if not isinstance(raw_badge, dict):
            continue
        identity = _badge_identity(raw_badge, forced_channel_id)
        if identity is None:
            continue
        set_id, version, effective_channel_id = identity
        # A legacy response's other collection is not evidence for this source.
        if (
            (replace_global and effective_channel_id)
            or (not replace_global and not effective_channel_id)
            or (channel_id and effective_channel_id != channel_id)
        ):
            continue
        badge_key = (set_id, version)
        if effective_channel_id:
            previous = subscriber_badge_info.get(effective_channel_id, {})
            target = channel_badges.setdefault(effective_channel_id, {})
        else:
            previous = badge_info
            target = global_badges
        target[badge_key] = {
            **previous.get(badge_key, {}),
            **raw_badge,
            "image1x": raw_badge.get("image1x", raw_badge.get("imageUrlNormal")),
            "image2x": raw_badge.get("image2x", raw_badge.get("imageUrlDouble")),
            "image4x": raw_badge.get("image4x", raw_badge.get("imageUrlQuadruple")),
        }

    if badges and not global_badges and not any(channel_badges.values()):
        msg = "badge response contains no usable badge records"
        raise _BadgeShapeError(msg)

    if replace_global:
        badge_info.clear()
        badge_info.update(global_badges)
    for refreshed_channel_id, refreshed_badges in channel_badges.items():
        # Insertion order is the LRU order; replacement also counts as use.
        subscriber_badge_info.pop(refreshed_channel_id, None)
        subscriber_badge_info[refreshed_channel_id] = refreshed_badges
        while len(subscriber_badge_info) > _BADGE_CHANNEL_LIMIT:
            subscriber_badge_info.pop(next(iter(subscriber_badge_info)))

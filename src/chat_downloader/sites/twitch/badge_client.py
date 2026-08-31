# SPDX-License-Identifier: MIT

"""Twitch badge retrieval, fallback, normalization, and cache updates."""

from __future__ import annotations

import base64
from json import JSONDecodeError
from typing import TYPE_CHECKING

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import ChatDownloaderError, ParsingError
from chat_downloader.utils.json_types import get_dict, get_list, get_str

from .graphql_client import _PersistedQueryUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONDict, JSONList

    from ._protocols import _DownloadGQL, _SessionPost


class _BadgeShapeError(ParsingError):
    """Raised when a Twitch badge response has drifted containers."""


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
    sources: tuple[
        tuple[str, Callable[[], tuple[JSONList, str | None]]],
        ...,
    ] = (
        (
            "channel",
            lambda: _download_channel_badges(
                session_post,
                channel,
                channel_id,
                download_gql_func,
                client_id,
            ),
        ),
        (
            "global",
            lambda: _download_global_badges(
                session_post,
                download_gql_func,
                client_id,
            ),
        ),
    )
    for source_name, fetch_badges in sources:
        try:
            badges, forced_channel_id = fetch_badges()
            _store_badges(
                badges,
                forced_channel_id,
                badge_info,
                subscriber_badge_info,
            )
        except (
            RequestException,
            JSONDecodeError,
            KeyError,
            ValueError,
            ChatDownloaderError,
        ) as error:
            log(
                "warning",
                f"Failed to retrieve {source_name} badge information for channel "
                f"'{channel}': {type(error).__name__}: {error}. Continuing "
                "without badges from this source.",
            )


def _download_channel_badges(
    session_post: _SessionPost,
    channel: str,
    channel_id: str | None,
    download_gql_func: _DownloadGQL,
    client_id: str | None,
) -> tuple[JSONList, str | None]:
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
        return _get_user_badges(data), channel_id
    badges = _get_badge_list(data, "badges")
    badges.extend(_get_user_badges(data))
    return badges, None


def _download_global_badges(
    session_post: _SessionPost,
    download_gql_func: _DownloadGQL,
    client_id: str | None,
) -> tuple[JSONList, str | None]:
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
        return _get_badge_list(data, "badges"), ""
    badges = _get_badge_list(data, "badges")
    badges.extend(_get_user_badges(data))
    return badges, None


def _get_response_data(response: JSONList) -> JSONDict:
    """Return the first GraphQL data object or reject a drifted container."""
    if not response or not isinstance(response[0], dict):
        msg = "badge response does not contain an operation object"
        raise _BadgeShapeError(msg)
    raw_data = response[0].get("data")
    if not isinstance(raw_data, dict):
        msg = "badge response data is not an object"
        raise _BadgeShapeError(msg)
    return get_dict(response[0], "data")


def _get_badge_list(container: JSONDict, key: str) -> JSONList:
    """Return an optional badge list or reject a drifted list container."""
    raw_badges = container.get(key)
    if raw_badges is None:
        return []
    if not isinstance(raw_badges, list):
        msg = f"badge response {key} is not a list"
        raise _BadgeShapeError(msg)
    return get_list(container, key)


def _get_user_badges(data: JSONDict) -> JSONList:
    """Return optional nested broadcast badges with container validation."""
    raw_user = data.get("user")
    if raw_user is None:
        return []
    if not isinstance(raw_user, dict):
        msg = "badge response user is not an object"
        raise _BadgeShapeError(msg)
    return _get_badge_list(get_dict(data, "user"), "broadcastBadges")


def _store_badges(
    badges: JSONList,
    forced_channel_id: str | None,
    badge_info: dict[tuple[str, str], JSONDict],
    subscriber_badge_info: dict[str, dict[tuple[str, str], JSONDict]],
) -> None:
    """Normalize and store one legacy or mobile badge collection."""
    for raw_badge in badges:
        if not isinstance(raw_badge, dict):
            continue
        if forced_channel_id is None:
            try:
                set_id, version, effective_channel_id, *_ = (
                    base64.b64decode(get_str(raw_badge, "id"))
                    .decode()
                    .strip()
                    .split(";")
                )
            except (ValueError, KeyError) as badge_error:
                log(
                    "debug",
                    f"Skipping malformed badge (id={raw_badge.get('id')!r}): "
                    f"{badge_error}",
                )
                continue
        else:
            set_id = get_str(raw_badge, "setID")
            version = get_str(raw_badge, "version")
            effective_channel_id = forced_channel_id
            if not set_id or not version:
                log("debug", "Skipping malformed mobile badge without set/version")
                continue

        badge_key = (set_id, version)
        if effective_channel_id:
            target = subscriber_badge_info.setdefault(effective_channel_id, {})
        else:
            target = badge_info
        badge = {
            **target.get(badge_key, {}),
            **raw_badge,
            "image1x": raw_badge.get("image1x", raw_badge.get("imageUrlNormal")),
            "image2x": raw_badge.get("image2x", raw_badge.get("imageUrlDouble")),
            "image4x": raw_badge.get("image4x", raw_badge.get("imageUrlQuadruple")),
        }
        target[badge_key] = badge

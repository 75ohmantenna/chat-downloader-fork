# SPDX-License-Identifier: MIT

"""Twitch GraphQL request helpers."""

from __future__ import annotations

import base64
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_list, get_str
from chat_downloader.utils.string_utils import contains_any_hint

from .constants import CLIENT_ID, GQL_API_URL, OPERATION_HASHES

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONAny, JSONList

    from ._protocols import _DownloadGQL, _SessionPost

GQL_AUTH_COOKIE_NAME: str = "auth-token"

_CHALLENGE_HINTS: tuple[str, ...] = (
    "captcha",
    "challenge",
    "kasada",
    "verify you are human",
)


def _contains_challenge_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    return contains_any_hint(text, _CHALLENGE_HINTS)


def _download_base_gql(
    session_post: _SessionPost,
    ops: JSONList,
    auth_token: str | None = None,
    client_id: str | None = None,
) -> JSONAny:
    """Download GraphQL data using base query payloads."""
    headers: dict[str, str] = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Client-ID": client_id or CLIENT_ID,
    }
    if auth_token:
        headers["Authorization"] = "OAuth " + auth_token
    response = session_post(
        GQL_API_URL,
        json=ops,
        headers=headers,
    )
    status_code = getattr(response, "status_code", 200)
    body_text = getattr(response, "text", "")
    if status_code >= 400 and _contains_challenge_text(body_text):
        msg = (
            "Twitch is requiring a captcha/challenge for GraphQL requests. "
            f"HTTP {status_code}. Try fresh cookies or "
            "--request_profile twitch_web."
        )
        raise CaptchaChallengeRequired(
            msg,
        )
    if status_code >= 400:
        response.raise_for_status()
    return response.json()


def _describe_operation_names(operation_names: list[str] | None) -> str:
    """Return a compact operation-name description for error messages."""
    if not operation_names:
        return "unknown operation"
    if len(operation_names) == 1:
        return operation_names[0]
    return ", ".join(operation_names)


def _handle_gql_errors(
    errors: JSONList,
    operation_names: list[str] | None = None,
) -> None:
    """Handle GraphQL errors by mapping them to downloader exceptions."""
    from chat_downloader.errors import (
        LoginRequired,
        ParsingError,
        VideoNotFound,
        VideoUnavailable,
        VideoUnplayable,
    )

    if not errors:
        return

    error = errors[0]
    if not isinstance(error, dict):
        return
    error_message = get_str(error, "message", "Unknown GraphQL error")
    error_path = get_list(error, "path")
    message_lower = error_message.lower()
    operation_text = _describe_operation_names(operation_names)

    if "not found" in message_lower or "does not exist" in message_lower:
        raise VideoNotFound(error_message)
    if "unauthorized" in message_lower or "not authorized" in message_lower:
        msg = f"Authentication required: {error_message}"
        raise LoginRequired(msg)
    if "subscriber" in message_lower or "subscription" in message_lower:
        msg = f"Subscriber-only content requires login: {error_message}"
        raise VideoUnplayable(
            msg,
        )
    if "unavailable" in message_lower or "deleted" in message_lower:
        raise VideoUnavailable(error_message)
    if "service error" in message_lower:
        path_str = " -> ".join(str(p) for p in error_path) if error_path else "unknown"
        log(
            "warning",
            f"Transient GraphQL field error at {path_str}: {error_message} (skipping)",
        )
        return
    if (
        "persistedquerynotfound" in message_lower
        or "persisted query not found" in message_lower
    ):
        msg = (
            "Twitch persisted GraphQL query failed for "
            f"{operation_text}: {error_message}. "
            "Operation hashes or required variables may be stale."
        )
        raise ParsingError(
            msg,
        )

    path_str = " -> ".join(str(p) for p in error_path) if error_path else "unknown"
    msg = f"GraphQL error at {path_str} during {operation_text}: {error_message}"
    raise ParsingError(
        msg,
    )


def _download_gql(
    session_post: _SessionPost,
    ops: JSONList,
    auth_token: str | None = None,
    client_id: str | None = None,
) -> JSONList:
    """Download GraphQL data using persisted query hashes."""
    operation_names = [
        str(op.get("operationName", "")) if isinstance(op, dict) else "" for op in ops
    ]
    missing_operation_names = [
        operation_name
        for operation_name in operation_names
        if operation_name not in OPERATION_HASHES
    ]
    if missing_operation_names:
        from chat_downloader.errors import ParsingError
        from chat_downloader.metadata import __version__

        missing_text = ", ".join(missing_operation_names)
        msg = (
            "Missing Twitch persisted GraphQL hash mapping for "
            f"{missing_text}. chat-downloader-fork {__version__} ships a "
            "fixed set of hashes; Twitch may have rotated them. Update "
            "OPERATION_HASHES in "
            "src/chat_downloader/sites/twitch/constants.py — current values "
            "can be observed in the Twitch web client's Network tab "
            "(look for GraphQL POST requests carrying a "
            "persistedQuery.sha256Hash extension)."
        )
        raise ParsingError(
            msg,
        )

    request_ops: list[dict[str, object]] = [
        {
            **op,
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": OPERATION_HASHES[str(op.get("operationName", ""))],
                },
            },
        }
        for op in ops
        if isinstance(op, dict)
    ]
    result = _download_base_gql(
        session_post, cast("JSONList", request_ops), auth_token, client_id
    )

    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "errors" in item:
                _handle_gql_errors(cast("JSONList", item["errors"]), operation_names)
    elif isinstance(result, dict) and "errors" in result:
        _handle_gql_errors(cast("JSONList", result["errors"]), operation_names)

    return cast("JSONList", result)


def update_badge_info(
    session_post: _SessionPost,
    channel: str,
    download_gql_func: _DownloadGQL,
    badge_info: dict[tuple[str, str], dict[str, Any]],
    subscriber_badge_info: dict[str, dict[tuple[str, str], dict[str, Any]]],
    client_id: str | None = None,
) -> None:
    """Update badge information cache for a channel."""
    try:
        query: JSONList = [
            {
                "operationName": "ChatList_Badges",
                "variables": {"channelLogin": channel},
            },
        ]
        gquery: JSONList = [{"operationName": "GlobalBadges"}]
        channel_data = (
            multi_get(
                download_gql_func(session_post, query, client_id=client_id),
                0,
                "data",
            )
            or {}
        )
        global_data = (
            multi_get(
                download_gql_func(session_post, gquery, client_id=client_id),
                0,
                "data",
            )
            or {}
        )

        badges = (channel_data.get("badges") or []) + (global_data.get("badges") or [])
        user = (multi_get(channel_data, "user", "broadcastBadges") or []) + (
            multi_get(global_data, "user", "broadcastBadges") or []
        )

        for badge in badges + user:
            try:
                set_id, version, channel_id, *_ = (
                    base64.b64decode(badge["id"]).decode().strip().split(";")
                )
            except (ValueError, KeyError) as badge_error:
                log(
                    "debug",
                    f"Skipping malformed badge (id={badge.get('id')!r}): {badge_error}",
                )
                continue

            if channel_id:
                subscriber_badge_info.setdefault(channel_id, {})
                subscriber_badge_info[channel_id][(set_id, version)] = badge
            else:
                badge_info[(set_id, version)] = badge

    except (RequestException, JSONDecodeError, KeyError, ValueError) as error:
        log(
            "warning",
            f"Failed to retrieve badge information for channel '{channel}': "
            f"{type(error).__name__}: {error}. Continuing without badges.",
        )

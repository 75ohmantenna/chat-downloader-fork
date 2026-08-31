# SPDX-License-Identifier: MIT

"""Twitch GraphQL request helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from chat_downloader.debugging import log
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    LoginRequired,
    ParsingError,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.metadata import __version__
from chat_downloader.utils.json_types import get_list, get_str
from chat_downloader.utils.string_utils import contains_any_hint

from .constants import (
    CLIENT_ID,
    GQL_API_URL,
    OPERATION_HASHES,
    PERSISTED_OPERATION_NAMES,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.utils.json_types import JSONAny, JSONDict, JSONList

    from ._protocols import _SessionPost

GQL_AUTH_COOKIE_NAME: str = "auth-token"

_FULL_QUERY_DOCUMENTS: dict[str, str] = {
    "StreamMetadata": (
        "query StreamMetadata($channelLogin: String!) { user(login: "
        "$channelLogin) { id login displayName lastBroadcast { title } "
        "stream { id type } } }"
    ),
    "VideoMetadata": (
        "query VideoMetadata($videoID: ID!) { video(id: $videoID) { id title "
        "lengthSeconds owner { id login } } }"
    ),
    "VideoCommentsQuery": (
        "query VideoCommentsQuery($vodId: ID!, $after: Cursor, "
        "$contentOffsetSeconds: Int) { video(id: $vodId) { comments(after: "
        "$after, contentOffsetSeconds: $contentOffsetSeconds) { edges { cursor "
        "node { __typename ...VideoCommentChommentModelFragment } } } } }  "
        "fragment VideoCommentChommentModelFragment on VideoComment { commenter "
        "{ id displayName login } contentOffsetSeconds createdAt id message { "
        "fragments { emote { from emoteID to } text } userBadges { setID version "
        "} userColor } video { id owner { id } } }"
    ),
}

_FULL_QUERY_OMITTED_VARIABLES: dict[str, frozenset[str]] = {
    "StreamMetadata": frozenset({"includeIsDJ"}),
    "VideoMetadata": frozenset({"channelLogin"}),
}


class _PersistedQueryUnavailable(ParsingError):
    """Raised when Twitch no longer recognizes a persisted operation hash."""


_CHALLENGE_HINTS: tuple[str, ...] = (
    "captcha",
    "challenge",
    "kasada",
    "verify you are human",
)

_OPTIONAL_SERVICE_ERROR_PATHS: tuple[tuple[str, ...], ...] = (("user", "primaryTeam"),)


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
) -> bool:
    """Handle GraphQL errors by mapping them to downloader exceptions."""
    if not errors:
        return False

    operation_text = _describe_operation_names(operation_names)
    optional_degradation_found = False
    for error in errors:
        if not isinstance(error, dict):
            continue
        error_message = get_str(error, "message", "Unknown GraphQL error")
        error_path = get_list(error, "path")
        message_lower = error_message.lower()

        if (
            "persistedquerynotfound" in message_lower
            or "persisted query not found" in message_lower
        ):
            msg = (
                "Twitch persisted GraphQL query failed for "
                f"{operation_text}: {error_message}. "
                "Operation hashes or required variables may be stale."
            )
            raise _PersistedQueryUnavailable(
                msg,
            )
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
            optional_degradation_found |= _log_service_error(
                error_message,
                error_path,
            )
            continue
        path_str = " -> ".join(str(p) for p in error_path) if error_path else "unknown"
        msg = f"GraphQL error at {path_str} during {operation_text}: {error_message}"
        raise ParsingError(
            msg,
        )
    return optional_degradation_found


def _log_service_error(error_message: str, error_path: JSONList) -> bool:
    """Log one service error and return whether its path is optional."""
    path_str = " -> ".join(str(p) for p in error_path) if error_path else "unknown"
    is_optional = tuple(error_path) in _OPTIONAL_SERVICE_ERROR_PATHS
    level = "debug" if is_optional else "warning"
    log(
        level,
        f"Transient GraphQL field error at {path_str}: {error_message} (skipping)",
    )
    return is_optional


def _download_gql(
    session_post: _SessionPost,
    ops: JSONList,
    auth_token: str | None = None,
    client_id: str | None = None,
    *,
    record_optional_degradation: Callable[[], None] | None = None,
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
            "operationName": PERSISTED_OPERATION_NAMES.get(
                str(op.get("operationName", "")),
                str(op.get("operationName", "")),
            ),
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

    try:
        _handle_result_errors(
            result,
            operation_names,
            record_optional_degradation=record_optional_degradation,
        )
    except _PersistedQueryUnavailable:
        fallback_ops = _build_full_query_ops(ops)
        if fallback_ops is None:
            raise
        result = _download_base_gql(
            session_post,
            fallback_ops,
            auth_token,
            client_id,
        )
        _handle_result_errors(
            result,
            operation_names,
            record_optional_degradation=record_optional_degradation,
        )

    return cast("JSONList", result)


def _handle_result_errors(
    result: JSONAny,
    operation_names: list[str],
    *,
    record_optional_degradation: Callable[[], None] | None = None,
) -> None:
    """Raise mapped errors found in a GraphQL response."""
    optional_degradation_count = 0
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "errors" in item:
                optional_degradation_count += _handle_gql_errors(
                    cast("JSONList", item["errors"]),
                    operation_names,
                )
    elif isinstance(result, dict) and "errors" in result:
        optional_degradation_count += _handle_gql_errors(
            cast("JSONList", result["errors"]),
            operation_names,
        )
    if record_optional_degradation is not None:
        for _ in range(optional_degradation_count):
            record_optional_degradation()


def _build_full_query_ops(ops: JSONList) -> JSONList | None:
    """Build full-document fallbacks when every operation is supported."""
    fallback_ops: list[dict[str, object]] = []
    for raw_op in ops:
        # _download_gql rejects non-dict operations before making a request.
        op = cast("JSONDict", raw_op)
        operation_name = get_str(op, "operationName")
        query = _FULL_QUERY_DOCUMENTS.get(operation_name)
        if query is None:
            return None
        raw_variables = op.get("variables")
        variables = dict(raw_variables) if isinstance(raw_variables, dict) else {}
        for omitted_variable in _FULL_QUERY_OMITTED_VARIABLES.get(
            operation_name, frozenset()
        ):
            variables.pop(omitted_variable, None)
        fallback_ops.append(
            {
                "operationName": operation_name,
                "variables": variables,
                "query": query,
            }
        )
    return cast("JSONList", fallback_ops)

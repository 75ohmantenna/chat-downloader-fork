# SPDX-License-Identifier: MIT

"""Twitch replay chat orchestration helpers."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from logging import DEBUG
from typing import TYPE_CHECKING, Protocol, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import debug_log, log, logger
from chat_downloader.errors import (
    NoChatReplay,
    ParsingError,
    RetriesExceeded,
    UserNotFound,
    VideoUnavailable,
)
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites.models import Chat
from chat_downloader.sites.retry import _attempt_numbers
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_float, get_list, get_str

from ._replay_vod_loop import _classify_empty_page, _init_vod_loop
from .constants import TWITCH_DEBUG_SAMPLE_LIMIT, build_known_comment_keys
from .graphql_client import _handle_gql_errors
from .parsing.messages import _parse_item
from .replay_transport import get_chat_messages_by_vod_id
from .validation_keys import find_unexpected_vod_edge_paths

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from logging import Logger

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
    from chat_downloader.utils.json_types import JSONDict, JSONList

    from .extractor import TwitchChatDownloader
    from .types import BadgeSet


class _FetchMessages(Protocol):
    """Callable that fetches one page of Twitch VOD replay comments."""

    def __call__(
        self,
        session_post: object,
        download_gql_func: Callable[[JSONList], list[JSONDict]],
        vod_id: str,
        cursor: str | None,
        content_offset_seconds: float | None,
    ) -> tuple[JSONDict | None, JSONDict | None]: ...


def _process_vod_edge(
    edge: JSONDict,
    offset: float,
    creator_channel_id: str | None,
    badge_set: BadgeSet,
    time_filter: TimeRangeFilter,
    msg_filter: MessageFilter,
    logger_obj: Logger,
) -> tuple[JSONDict | None, str]:
    """Process a single VOD comment edge from a GraphQL response page.

    Validates the edge and node typename, applies time and message filters, and
    returns a disposition string that the caller uses to decide next action.

    Args:
        edge: A single edge dict from the GraphQL ``edges`` list.
        offset: Clip or segment offset in seconds to pass to ``_parse_item``.
        creator_channel_id: Channel ID of the VOD creator, may be ``None``.
        badge_set: Badge snapshot to pass to ``_parse_item``.
        time_filter: Object with a ``check(data)`` method returning ``"yield"``,
            ``"skip"``, or ``"stop"``.
        msg_filter: Object with a ``should_add(data)`` method returning bool.
        logger_obj: Logger-compatible object used for debug messages.

    Returns:
        A ``(data, disposition)`` tuple where disposition is one of:

        - ``"yield"`` — caller should yield *data* to the consumer.
        - ``"skip"`` — caller should skip to the next edge.
        - ``"stop"`` — caller must ``return`` (end the generator).
    """
    unexpected_paths = (
        find_unexpected_vod_edge_paths(edge) if logger_obj.isEnabledFor(DEBUG) else []
    )
    if unexpected_paths:
        capture_debug_sample(
            "twitch-unknown-gql-shape",
            {
                "raw": edge,
                "unexpected_paths": unexpected_paths,
            },
            sample_limit=TWITCH_DEBUG_SAMPLE_LIMIT,
        )
        debug_log(
            f"Unexpected Twitch GraphQL paths: {unexpected_paths}",
            f"Original edge: {edge}",
        )

    edge_typename = edge.get("__typename")
    if edge_typename not in ("VideoCommentEdge", None):
        logger_obj.debug("Skipping unexpected edge type: %s", edge_typename)
        return None, "skip"

    node = get_dict(edge, "node")
    if not node:
        return None, "skip"

    node_typename = node.get("__typename")
    if node_typename not in ("Comment", "VideoComment", None):
        logger_obj.debug("Skipping unexpected node type: %s", node_typename)
        return None, "skip"

    owner = get_dict(get_dict(node, "video"), "owner")
    effective_creator_channel_id = creator_channel_id or get_str(owner, "id")

    data: JSONDict = cast(
        "JSONDict",
        _parse_item(node, offset, effective_creator_channel_id, badge_set),
    )
    unexpected_keys = data.keys() - build_known_comment_keys()
    if unexpected_keys:
        capture_debug_sample(
            "twitch-unknown-gql-shape",
            {
                "raw": edge,
                "unexpected_output_keys": sorted(unexpected_keys),
                "parsed": data,
            },
            sample_limit=TWITCH_DEBUG_SAMPLE_LIMIT,
        )
        debug_log(
            f"Unexpected keys found: {unexpected_keys}",
            f"Original data: {node}",
            f"Parsed data: {data}",
            node.keys(),
            build_known_comment_keys(),
        )

    result = time_filter.check(data)
    if result == "skip":
        return None, "skip"
    if result == "stop":
        return None, "stop"

    if not msg_filter.should_add(data):
        return None, "skip"

    return data, "yield"


def _fetch_gql_one[T](
    downloader: TwitchChatDownloader,
    fetch_fn: Callable[[], T],
    request: ChatRequest,
) -> T:
    """Fetch a single GQL result with retry handling.

    Returns the result of ``fetch_fn()`` on success; raises on exhausted
    retries.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        try:
            return fetch_fn()
        except (
            JSONDecodeError,
            RequestException,
            KeyError,
            ParsingError,
        ) as error:
            downloader.retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover


def _fetch_vod_page(
    downloader: TwitchChatDownloader,
    fetch_fn: _FetchMessages,
    vod_id: str,
    cursor: str,
    content_offset: float,
    request: ChatRequest,
) -> tuple[JSONDict | None, JSONDict | None]:
    """Fetch one page of VOD comments with retry handling.

    Returns ``(comments, info)`` on success; raises on exhausted retries.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        try:
            return fetch_fn(
                downloader._session_post,
                downloader._download_gql,
                vod_id,
                cursor or None,
                content_offset,
            )
        except (JSONDecodeError, RequestException) as error:
            downloader.retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover


def iter_vod_chat_messages(  # noqa: C901 — cursor-advance guard, first-iteration check, and edge disposition fan-out are intrinsic to the VOD replay loop
    downloader: TwitchChatDownloader,
    vod_id: str,
    request: ChatRequest,
    max_duration: float | None,
    offset: float | None = None,
    fetch_messages: _FetchMessages | None = None,
    logger_obj: Logger | None = None,
) -> Generator[JSONDict, None, None]:
    """Yield replay chat messages for a VOD or clip."""
    fetch_messages = fetch_messages or get_chat_messages_by_vod_id
    effective_logger: Logger = logger_obj or logger

    plan = _init_vod_loop(request, max_duration, offset)

    message_count = 0
    cursor = ""
    first_iteration = True
    badge_set = downloader.badge_cache.snapshot()
    consecutive_empty_pages = 0
    max_empty_pages = 3

    while True:
        comments, info = _fetch_vod_page(
            downloader,
            fetch_messages,
            vod_id,
            cursor,
            plan.content_offset_seconds,
            request,
        )

        if first_iteration and info:
            creator_id = multi_get(info, "creator", "id")
            if creator_id == "":
                msg = f'Channel for VOD "{vod_id}" not found or has been deleted'
                raise UserNotFound(
                    msg,
                )
            first_iteration = False

        if not comments:
            break

        edges = get_list(comments, "edges")
        has_next_page = bool(multi_get(comments, "pageInfo", "hasNextPage"))

        if not edges:
            consecutive_empty_pages += 1
            page_action = _classify_empty_page(
                consecutive=consecutive_empty_pages,
                max_empty=max_empty_pages,
                has_next_page=has_next_page,
                vod_id=vod_id,
            )
            if page_action == "break":
                break
            continue

        consecutive_empty_pages = 0
        creator_channel_id = multi_get(info or {}, "creator", "channel", "id")
        previous_cursor = cursor
        for edge_item in edges:
            if not isinstance(edge_item, dict):
                continue
            new_cursor = get_str(edge_item, "cursor")
            if new_cursor:
                cursor = new_cursor
            data, edge_action = _process_vod_edge(
                edge_item,
                plan.offset,
                creator_channel_id,
                badge_set,
                plan.time_filter,
                plan.msg_filter,
                effective_logger,
            )
            if edge_action == "stop":
                return
            if edge_action == "skip":
                continue

            message_count += 1
            if data is None:
                msg = "Unexpected None data for non-skip edge"
                raise ValueError(msg)
            yield data

        log("debug", f"Total number of messages: {message_count}")

        if not has_next_page:
            break
        # Cursor must advance on a page that had edges; if Twitch returns
        # the same cursor we'd loop on identical data.
        if cursor == previous_cursor:
            log(
                "warning",
                f"VOD {vod_id}: cursor did not advance after a non-empty "
                "page; stopping pagination.",
            )
            break


def get_chat_by_vod_id(
    downloader: TwitchChatDownloader,
    vod_id: str,
    request: ChatRequest,
) -> Chat:
    """Build a replay chat object for a VOD."""
    query = [
        {
            "operationName": "VideoMetadata",
            "variables": {"channelLogin": "", "videoID": vod_id},
        },
    ]

    def _fetch_video() -> JSONDict | None:
        raw = downloader._download_gql(query)
        return cast("JSONDict | None", raw[0]["data"]["video"])

    video: JSONDict | None = _fetch_gql_one(downloader, _fetch_video, request)

    if not video:
        msg = "Sorry. Unless you've got a time machine, that content is unavailable."
        raise VideoUnavailable(
            msg,
        )

    title: str | None = get_str(video, "title") or None
    duration: float | None = get_float(video, "lengthSeconds") or None
    channel_login = multi_get(video, "owner", "login")
    if channel_login:
        downloader._update_badge_info(channel_login)

    return Chat(
        downloader._get_chat_messages_by_vod_id(vod_id, request, duration),
        title=title,
        duration=duration,
        status="past",
        video_type="video",
        id=vod_id,
    )


def get_chat_by_clip_id(
    downloader: TwitchChatDownloader,
    clip_id: str,
    request: ChatRequest,
) -> Chat:
    """Build a replay chat object for a clip."""
    query = {
        "query": (
            "query($slug: ID!) { clip(slug: $slug) { broadcaster { id login } "
            "video { id createdAt } createdAt durationSeconds "
            "videoOffsetSeconds title url slug } }"
        ),
        "variables": {"slug": clip_id},
    }

    def _fetch_clip() -> JSONDict | None:
        response = downloader._download_base_gql(query)
        if isinstance(response, dict) and "errors" in response:
            _handle_gql_errors(response["errors"], ["clip"])
        return cast("JSONDict | None", multi_get(response, "data", "clip"))

    clip: JSONDict | None = _fetch_gql_one(downloader, _fetch_clip, request)

    if clip is None:
        msg = f'Unable to retrieve clip data for "{clip_id}"'
        raise ParsingError(msg)

    vod_id = multi_get(clip, "video", "id")
    if vod_id is None:
        msg = (
            "This clip's past broadcast has expired and chat replay "
            "is no longer available."
        )
        raise NoChatReplay(
            msg,
        )

    offset: float | None = get_float(clip, "videoOffsetSeconds") or None
    duration: float | None = get_float(clip, "durationSeconds") or None
    downloader._update_badge_info(multi_get(clip, "broadcaster", "login"))

    return Chat(
        downloader._get_chat_messages_by_vod_id(vod_id, request, duration, offset),
        title=f"{get_str(clip, 'title')} ({clip_id})",
        duration=duration,
        status="past",
        video_type="clip",
        id=clip_id,
    )

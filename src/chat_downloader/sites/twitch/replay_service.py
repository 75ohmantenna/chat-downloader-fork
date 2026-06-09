# SPDX-License-Identifier: MIT

"""Twitch replay chat orchestration helpers."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import debug_log, log, logger
from chat_downloader.errors import (
    NoChatReplay,
    ParsingError,
    RetriesExceeded,
    UserNotFound,
    VideoUnavailable,
)
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.retry import _attempt_numbers
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.time_utils import ensure_seconds

from .constants import MESSAGE_GROUPS, build_known_comment_keys
from .graphql_client import _handle_gql_errors
from .parsing.messages import _parse_item
from .replay_transport import get_chat_messages_by_vod_id

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.models import ChatRequest

    from .extractor import TwitchChatDownloader


def _process_vod_edge(
    edge: dict[str, Any],
    offset: float,
    creator_channel_id: str | None,
    badge_set: Any,
    time_filter: Any,
    msg_filter: Any,
    logger_obj: Any,
) -> tuple[dict[str, Any] | None, str]:
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
    edge_typename = edge.get("__typename")
    if edge_typename not in ("VideoCommentEdge", None):
        logger_obj.debug(f"Skipping unexpected edge type: {edge_typename}")
        return None, "skip"

    node = edge.get("node")
    if not node:
        return None, "skip"

    node_typename = node.get("__typename")
    if node_typename not in ("Comment", "VideoComment", None):
        logger_obj.debug(f"Skipping unexpected node type: {node_typename}")
        return None, "skip"

    data = _parse_item(node, offset, creator_channel_id, badge_set)
    unexpected_keys = data.keys() - build_known_comment_keys()
    if unexpected_keys:
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
    fetch_fn: Callable[..., Any],
    vod_id: str,
    cursor: str,
    content_offset: float,
    request: ChatRequest,
) -> tuple[Any, Any]:
    """Fetch one page of VOD comments with retry handling.

    Returns ``(comments, info)`` on success; raises on exhausted retries.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        try:
            return cast(
                "tuple[Any, Any]",
                fetch_fn(
                    downloader._session_post,
                    downloader._download_gql,
                    vod_id,
                    cursor or None,
                    content_offset,
                ),
            )
        except (JSONDecodeError, RequestException) as error:
            downloader.retry(attempt_number, error=error, request=request)
    raise RetriesExceeded(request.max_attempts)  # pragma: no cover


def iter_vod_chat_messages(
    downloader: TwitchChatDownloader,
    vod_id: str,
    request: ChatRequest,
    max_duration: float | None,
    offset: float | None = None,
    fetch_messages: Callable[..., Any] | None = None,
    logger_obj: Any = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield replay chat messages for a VOD or clip."""
    fetch_messages = fetch_messages or get_chat_messages_by_vod_id
    logger_obj = logger_obj or logger

    start_time = ensure_seconds(request.start_time, 0)
    end_value = request.end_time
    if offset is None:
        offset = 0
        end_time = ensure_seconds(end_value)
        content_offset_seconds = (
            start_time
            if max_duration is None
            else min(start_time, max_duration)
        )
    else:
        end_time = ensure_seconds(end_value, max_duration)
        content_offset_seconds = (start_time or 0) + offset

    msg_filter = MessageFilter(
        MESSAGE_GROUPS,
        request.message_groups
        if isinstance(request.message_groups, list)
        else None,
        request.message_types or [],
    )
    time_filter = TimeRangeFilter(start_time, end_time, skip_mode="always")

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
            content_offset_seconds,
            request,
        )

        if first_iteration and info:
            creator_id = multi_get(info, "creator", "id")
            if creator_id == "":
                msg = (
                    f'Channel for VOD "{vod_id}" not found or has been deleted'
                )
                raise UserNotFound(
                    msg,
                )
            first_iteration = False

        if not comments:
            break

        edges = comments.get("edges") or []
        has_next_page = bool(multi_get(comments, "pageInfo", "hasNextPage"))

        if not edges:
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= max_empty_pages:
                log(
                    "warning",
                    f"VOD {vod_id}: {max_empty_pages} consecutive empty "
                    "pages with hasNextPage=true and no cursor advance; "
                    "stopping pagination to avoid an infinite loop.",
                )
                break
            if not has_next_page:
                break
            continue

        consecutive_empty_pages = 0
        creator_channel_id = multi_get(info or {}, "creator", "channel", "id")
        previous_cursor = cursor
        for edge in edges:
            new_cursor = edge.get("cursor")
            if new_cursor:
                cursor = new_cursor
            data, disposition = _process_vod_edge(
                edge,
                offset,
                creator_channel_id,
                badge_set,
                time_filter,
                msg_filter,
                logger_obj,
            )
            if disposition == "stop":
                return
            if disposition == "skip":
                continue

            message_count += 1
            assert data is not None
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

    video: Any = _fetch_gql_one(
        downloader,
        lambda: downloader._download_gql(query)[0]["data"]["video"],
        request,
    )

    if not video:
        msg = (
            "Sorry. Unless you've got a time machine, that content is "
            "unavailable."
        )
        raise VideoUnavailable(
            msg,
        )

    title = video.get("title")
    duration = video.get("lengthSeconds")
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

    def _fetch_clip() -> Any:
        response = downloader._download_base_gql(query)
        if isinstance(response, dict) and "errors" in response:
            _handle_gql_errors(response["errors"], ["clip"])
        return multi_get(response, "data", "clip")

    clip: Any = _fetch_gql_one(downloader, _fetch_clip, request)

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

    offset = clip.get("videoOffsetSeconds")
    duration = clip.get("durationSeconds")
    downloader._update_badge_info(multi_get(clip, "broadcaster", "login"))

    return Chat(
        downloader._get_chat_messages_by_vod_id(
            vod_id, request, duration, offset
        ),
        title=f"{clip.get('title')} ({clip_id})",
        duration=duration,
        status="past",
        video_type="clip",
        id=clip_id,
    )

# SPDX-License-Identifier: MIT

"""Twitch live chat orchestration helpers."""

from __future__ import annotations

from collections.abc import Generator
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import debug_log, log, logger
from chat_downloader.errors import ParsingError, UserNotFound
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat, _SeenMessageCache
from chat_downloader.sites.retry import _attempt_numbers
from chat_downloader.utils.dict_utils import multi_get

from .constants import MESSAGE_GROUPS, build_known_irc_keys
from .irc_transport import (
    _PROGRESS_LOG_INTERVAL_MESSAGES,
    TwitchChatIRC,
    get_chat_messages_by_stream_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest

    from .extractor import TwitchChatDownloader


def _is_duplicate_live_message(
    message_id: object,
    seen_message_cache: _SeenMessageCache,
) -> bool:
    if not isinstance(message_id, str) or not message_id:
        return False

    is_new, _evicted_message_id = seen_message_cache.register(message_id)
    return not is_new


def iter_stream_chat_messages(
    downloader: TwitchChatDownloader,
    stream_id: str,
    request: ChatRequest,
    irc_factory: Callable | None = None,
    message_generator: Callable | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield live IRC chat messages for a stream."""
    irc_factory = irc_factory or TwitchChatIRC
    message_generator = message_generator or get_chat_messages_by_stream_id
    msg_filter = MessageFilter(
        MESSAGE_GROUPS,
        request.message_groups
        if isinstance(request.message_groups, list)
        else None,
        request.message_types or [],
    )

    def create_connection() -> TwitchChatIRC:
        for attempt_number in _attempt_numbers(request.max_attempts):
            try:
                irc = irc_factory()
                irc.set_timeout(request.message_receive_timeout)
                irc.join_channel(stream_id)
                return irc
            except OSError as error:
                downloader.retry(attempt_number, error=error, request=request)
        raise RuntimeError(
            "unreachable: retry should have raised RetriesExceeded"
        )

    twitch_chat_irc = create_connection()
    message_count = 0
    badge_set = downloader.badge_cache.snapshot()
    seen_message_cache = _SeenMessageCache()

    try:
        while True:
            try:
                for raw_message in message_generator(
                    twitch_chat_irc,
                    stream_id,
                    request,
                    badge_set,
                ):
                    if raw_message.get("action_type") == "reconnect":
                        log(
                            "info",
                            "Twitch IRC server requested reconnect; "
                            "reconnecting.",
                        )
                        msg = "Server requested reconnect."
                        raise ConnectionError(msg)

                    if _is_duplicate_live_message(
                        raw_message.get("message_id"),
                        seen_message_cache,
                    ):
                        continue

                    unexpected_keys = (
                        raw_message.keys() - build_known_irc_keys()
                    )
                    if unexpected_keys:
                        debug_log(
                            f"Unexpected keys found: {unexpected_keys}",
                            f"Parsed data: {raw_message}",
                        )

                    if not msg_filter.should_add(raw_message):
                        continue

                    message_count += 1
                    if message_count % _PROGRESS_LOG_INTERVAL_MESSAGES == 0:
                        log(
                            "debug",
                            f"Total number of messages: {message_count}",
                        )
                    yield raw_message

            except ConnectionError:
                twitch_chat_irc.close_connection()
                twitch_chat_irc = create_connection()
                downloader._update_badge_info(stream_id)
                badge_set = downloader.badge_cache.snapshot()
            else:
                # The production IRC generator is open-ended. This branch
                # keeps injected finite generators testable and supports
                # future finite live transports.
                break
    finally:
        twitch_chat_irc.close_connection()


def get_chat_by_stream_id(
    downloader: TwitchChatDownloader,
    stream_id: str,
    request: ChatRequest,
) -> Chat:
    """Build a live chat object for a stream."""
    query = [
        {
            "operationName": "StreamMetadata",
            "variables": {
                "channelLogin": stream_id.lower(),
                "includeIsDJ": True,
            },
        },
    ]

    stream_info: Any = None
    for attempt_number in _attempt_numbers(request.max_attempts):
        try:
            stream_info = downloader._download_gql(query)[0]["data"]["user"]
            break
        except (
            JSONDecodeError,
            RequestException,
            ParsingError,
            KeyError,
            IndexError,
            TypeError,
        ) as error:
            downloader.retry(attempt_number, error=error, request=request)

    if not stream_info:
        msg = f'Unable to find user: "{stream_id}"'
        raise UserNotFound(msg)

    stream_type = multi_get(stream_info, "stream", "type")
    is_live = stream_type in ("live", "rerun")
    title = (
        multi_get(stream_info, "lastBroadcast", "title")
        if is_live
        else stream_id
    )

    if stream_type == "rerun":
        log("info", f'Channel "{stream_id}" is broadcasting a rerun')
        logger.debug(f"Stream status for {stream_id}: rerun")
    elif not is_live:
        log(
            "warning",
            f'Channel "{stream_id}" is not currently live. Waiting for '
            "stream to start...",
        )
        logger.debug(f"Stream status for {stream_id}: offline/upcoming")

    downloader._update_badge_info(stream_id)

    return Chat(
        downloader._get_chat_messages_by_stream_id(stream_id, request),
        title=title,
        duration=None,
        status="live" if is_live else "upcoming",
        video_type="video",
        id=stream_id,
    )

# SPDX-License-Identifier: MIT

"""Twitch live chat orchestration helpers."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, Protocol

from requests.exceptions import RequestException

from chat_downloader.debugging import debug_log, log, logger
from chat_downloader.errors import ParsingError, UserNotFound
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.proxy import resolve_session_proxy
from chat_downloader.sites.retry import _attempt_numbers, wait_for_reconnect
from chat_downloader.utils.dict_utils import multi_get

from .constants import IRC_HOST, MESSAGE_GROUPS, build_known_irc_keys
from .irc_transport import (
    _MIN_RECEIVE_TIMEOUT_SECONDS,
    _PROGRESS_LOG_INTERVAL_MESSAGES,
    TwitchChatIRC,
    get_chat_messages_by_stream_id,
)

# Twitch live IRC dedup window. Tuned for multi-day captures and reconnect
# storms; ~5 MB of IDs in memory at full capacity.
_LIVE_SEEN_MESSAGE_LIMIT = 50_000


class _IRCFactory(Protocol):
    """Callable that creates a configured Twitch IRC connection."""

    def __call__(
        self,
        *,
        connect_timeout: float,
        proxy_url: str | None,
    ) -> TwitchChatIRC: ...


class _MessageGenerator(Protocol):
    """Callable that yields raw IRC messages for a channel."""

    def __call__(
        self,
        irc: TwitchChatIRC,
        channel: str,
        params: ChatRequest,
        badge_set: BadgeSet,
    ) -> Generator[dict[str, Any], None, None]: ...


if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from .extractor import TwitchChatDownloader
    from .types import BadgeSet


def _is_duplicate_live_message(
    message_id: object,
    seen_message_cache: _SeenMessageCache,
) -> bool:
    if not isinstance(message_id, str) or not message_id:
        return False

    is_new, _evicted_message_id = seen_message_cache.register(message_id)
    return not is_new


def iter_stream_chat_messages(  # noqa: C901 — live IRC reconnect loop is intrinsically branchy
    downloader: TwitchChatDownloader,
    stream_id: str,
    request: ChatRequest,
    irc_factory: _IRCFactory | None = None,
    message_generator: _MessageGenerator | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield live IRC chat messages for a stream."""
    irc_factory = irc_factory or TwitchChatIRC
    message_generator = message_generator or get_chat_messages_by_stream_id
    msg_filter = MessageFilter.from_request(MESSAGE_GROUPS, request)

    def create_connection() -> TwitchChatIRC:
        connect_timeout = getattr(downloader, "_http_timeout", (10.0, 30.0))[0]
        proxy_url = resolve_session_proxy(
            getattr(downloader, "session", None),
            f"https://{IRC_HOST}",
        )
        for attempt_number in _attempt_numbers(request.max_attempts):
            irc: TwitchChatIRC | None = None
            try:
                irc = irc_factory(
                    connect_timeout=connect_timeout,
                    proxy_url=proxy_url,
                )
                irc.set_timeout(
                    max(
                        request.message_receive_timeout,
                        _MIN_RECEIVE_TIMEOUT_SECONDS,
                    )
                )
                irc.join_channel(stream_id)
            except OSError as error:
                if irc is not None:
                    irc.close_connection()
                downloader.retry(attempt_number, error=error, request=request)
            else:
                return irc
        msg_0 = "unreachable: retry should have raised RetriesExceeded"
        raise RuntimeError(msg_0)

    twitch_chat_irc = create_connection()
    message_count = 0
    badge_set = downloader.badge_cache.snapshot()
    # Live Twitch chat can deliver tens of msg/s on busy channels. A larger
    # window protects against duplicate emission after IRC reconnects,
    # which can replay several minutes of messages.
    seen_message_cache = _SeenMessageCache(limit=_LIVE_SEEN_MESSAGE_LIMIT)
    consecutive_connection_failures = 0

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
                            "Twitch IRC server requested reconnect; reconnecting.",
                        )
                        msg = "Server requested reconnect."
                        raise ConnectionError(msg)  # noqa: TRY301 — drives the outer reconnect loop via the enclosing except

                    # A normal IRC item proves the connection made progress.
                    # Future disconnects start a fresh bounded retry streak.
                    consecutive_connection_failures = 0

                    if _is_duplicate_live_message(
                        raw_message.get("message_id"),
                        seen_message_cache,
                    ):
                        continue

                    unexpected_keys = raw_message.keys() - build_known_irc_keys()
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

            except ConnectionError as error:
                twitch_chat_irc.close_connection()
                consecutive_connection_failures += 1
                wait_for_reconnect(
                    consecutive_connection_failures,
                    error=error,
                    request=request,
                    provider="Twitch IRC",
                )
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

    stream_info: JSONDict | None = None
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
    title = multi_get(stream_info, "lastBroadcast", "title") if is_live else stream_id

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

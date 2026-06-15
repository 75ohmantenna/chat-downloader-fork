# SPDX-License-Identifier: MIT

"""Kick live chat orchestration.

Fetches channel metadata, resolves the chatroom, emits preloaded history, then
streams live chat from the Pusher websocket — deduplicating across the two
sources and filtering by the requested message groups/types. The websocket
transport and frame iterator are injectable so this module is fully testable
without live Kick access.

The chatroom is active even when the channel is offline; the Pusher websocket
streams messages regardless of stream status.
"""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import log, logger
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.retry import _attempt_numbers

from .api_client import fetch_channel, fetch_preloaded_messages
from .constants import MESSAGE_GROUPS
from .errors import KickError, KickServerError
from .parsing.events import dispatch_event
from .parsing.messages import parse_preloaded_messages
from .websocket_transport import KickPusherTransport, read_frames

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.models import ChatRequest

    from .extractor import KickChatDownloader

_KICK_LIVE_SEEN_MESSAGE_LIMIT = 10_000


def _fetch_channel_with_retry(
    downloader: KickChatDownloader,
    username: str,
    request: ChatRequest,
) -> dict[str, Any]:
    """Fetch channel metadata, retrying transient failures.

    Terminal conditions (channel not found, Cloudflare challenge) propagate
    immediately; only transient server/network/JSON errors are retried.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        request: The active chat request (retry policy).

    Returns:
        The decoded channel metadata object.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        try:
            return fetch_channel(username)
        except (KickServerError, RequestException, JSONDecodeError) as error:
            downloader.retry(attempt_number, error=error, request=request)
    msg = "unreachable: retry should have raised RetriesExceeded"
    raise RuntimeError(msg)


def _resolve_channel(
    data: dict[str, Any],
    username: str,
) -> tuple[str, str, str]:
    """Resolve channel id, chatroom id, and title from metadata.

    The chatroom is active even when the channel is offline — this function
    does *not* reject offline channels.  If the channel is offline the title
    falls back to the username.

    Args:
        data: Channel metadata object.
        username: Channel username/slug.

    Returns:
        A ``(channel_id, chatroom_id, title)`` tuple.

    Raises:
        KickError: If the channel id or chatroom id is missing, making chat
            retrieval impossible.
    """
    raw_channel_id = data.get("id")
    if raw_channel_id is None:
        msg = f"Kick channel {username!r} metadata was missing a channel id."
        raise KickError(msg)

    chatroom = data.get("chatroom")
    raw_chatroom_id = chatroom.get("id") if isinstance(chatroom, dict) else None
    if raw_chatroom_id is None:
        msg = f"Kick channel {username!r} metadata was missing a chatroom id."
        raise KickError(msg)

    livestream = data.get("livestream")
    if not isinstance(livestream, dict):
        log("info", f'Kick channel "{username}" is offline; chatroom is still active.')
        title = username
    else:
        raw_title = livestream.get("session_title")
        title = str(raw_title) if raw_title else username

    return str(raw_channel_id), str(raw_chatroom_id), title


def _is_live_status(data: dict[str, Any]) -> bool:
    """Return ``True`` if the channel metadata indicates a live stream."""
    livestream = data.get("livestream")
    return isinstance(livestream, dict)


def get_chat_by_channel(
    downloader: KickChatDownloader,
    username: str,
    request: ChatRequest,
    *,
    transport_factory: Callable[[], KickPusherTransport] | None = None,
    frame_iterator: Callable[[KickPusherTransport], Any] | None = None,
) -> Chat:
    """Build a live :class:`Chat` for a Kick channel.

    Works for both live and offline channels — the chatroom is always active.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        request: The active chat request.
        transport_factory: Optional factory for the websocket transport
            (tests inject a fake).
        frame_iterator: Optional replacement for the live frame generator
            (tests inject a finite generator).

    Returns:
        A configured :class:`Chat` whose generator yields normalized messages.
    """
    data = _fetch_channel_with_retry(downloader, username, request)
    channel_id, chatroom_id, title = _resolve_channel(data, username)
    status = "live" if _is_live_status(data) else "idle"

    return Chat(
        _iter_chat_messages(
            downloader,
            username,
            channel_id,
            chatroom_id,
            request,
            transport_factory=transport_factory,
            frame_iterator=frame_iterator,
        ),
        title=title,
        status=status,
        video_type="video",
        id=username,
    )


def _open_subscribed_transport(
    downloader: KickChatDownloader,
    chatroom_id: str,
    request: ChatRequest,
    transport_factory: Callable[[], KickPusherTransport],
) -> KickPusherTransport:
    """Open a transport and subscribe to the chatroom, retrying failures.

    Args:
        downloader: The Kick downloader.
        chatroom_id: Chatroom id to subscribe to.
        request: The active chat request (retry policy and recv timeout).
        transport_factory: Factory producing a fresh transport.

    Returns:
        A connected, subscribed transport.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        transport = transport_factory()
        try:
            transport.connect(request.message_receive_timeout)
            transport.subscribe(chatroom_id)
        except ConnectionError as error:
            transport.close()
            downloader.retry(attempt_number, error=error, request=request)
        else:
            return transport
    msg = "unreachable: retry should have raised RetriesExceeded"
    raise RuntimeError(msg)


def _iter_chat_messages(  # noqa: C901 — live reconnect loop is intrinsically branchy
    downloader: KickChatDownloader,
    username: str,
    channel_id: str,
    chatroom_id: str,
    request: ChatRequest,
    *,
    transport_factory: Callable[[], KickPusherTransport] | None = None,
    frame_iterator: Callable[[KickPusherTransport], Any] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield normalized live chat messages for a channel."""
    transport_factory = transport_factory or KickPusherTransport
    frame_iterator = frame_iterator or read_frames

    msg_filter = MessageFilter(
        MESSAGE_GROUPS,
        request.message_groups if isinstance(request.message_groups, list) else None,
        request.message_types or [],
    )
    seen_message_cache = _SeenMessageCache(limit=_KICK_LIVE_SEEN_MESSAGE_LIMIT)

    def emit(message: dict[str, Any]) -> bool:
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message_id:
            is_new, _evicted = seen_message_cache.register(message_id)
            if not is_new:
                return False
        return msg_filter.should_add(message)

    # 1. Preloaded history (best-effort; non-fatal on failure).
    preloaded = fetch_preloaded_messages(channel_id, username)
    for message in parse_preloaded_messages(preloaded):
        if emit(message):
            yield message

    # 2. Live websocket feed with reconnect.
    transport = _open_subscribed_transport(
        downloader, chatroom_id, request, transport_factory
    )
    try:
        while True:
            try:
                for frame in frame_iterator(transport):
                    live_message = dispatch_event(frame)
                    if live_message is not None and emit(live_message):
                        yield live_message
            except ConnectionError as error:
                logger.debug("Kick websocket disconnected; reconnecting: %s", error)
                transport.close()
                transport = _open_subscribed_transport(
                    downloader, chatroom_id, request, transport_factory
                )
            else:
                break
    finally:
        transport.close()

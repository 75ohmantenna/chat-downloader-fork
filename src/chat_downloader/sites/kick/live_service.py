# SPDX-License-Identifier: MIT

"""Kick live chat orchestration.

Fetches channel metadata, resolves the chatroom, emits preloaded history, then
streams live chat from the Pusher WebSocket—deduplicating across the two
sources and filtering by the requested message groups/types. The WebSocket
transport and frame iterator are injectable so this module is fully testable
without live Kick access.

The chatroom is active even when the channel is offline; the Pusher WebSocket
streams messages regardless of stream status.
"""

from __future__ import annotations

import os
import time
from functools import partial
from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import log, logger
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    InvalidParameter,
    ParsingError,
    RetriesExceeded,
)
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.sites._seen_cache import _SeenMessageCache
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.proxy import resolve_session_proxy
from chat_downloader.sites.retry import _attempt_numbers, wait_for_reconnect

from .constants import KICK_DEBUG_SAMPLE_LIMIT, MESSAGE_GROUPS, is_numeric_id
from .errors import KickError, KickServerError
from .parsing.events import dispatch_event
from .parsing.messages import parse_preloaded_messages
from .parsing.pins import parse_pinned_message_created_event
from .pusher_discovery import _HttpClient, _RequestsHttpClient
from .websocket_transport import (
    _MIN_RECEIVE_TIMEOUT_SECONDS,
    KickPusherTransport,
    read_frames,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from .extractor import KickChatDownloader

_KICK_LIVE_SEEN_MESSAGE_LIMIT = 10_000
_SUCCESSFUL_FRAME_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES"
_SUCCESSFUL_FRAME_CAPTURE_LIMIT = 3
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class _KickLiveDiagnostics:
    """Mutable counters shared by Kick live orchestration and run summaries."""

    def __init__(self) -> None:
        self.summary: dict[str, object] = {
            "websocket_frame_count": 0,
            "control_frame_count": 0,
            "parsed_event_count": 0,
            "unsupported_event_count": 0,
            "unknown_message_type_count": 0,
            "malformed_event_count": 0,
            "invalid_websocket_frame_count": 0,
            "websocket_reconnect_count": 0,
            "pusher_error_count": 0,
            "pusher_key_recovery_count": 0,
            "last_websocket_frame_timestamp": None,
        }

    def increment(self, name: str) -> None:
        """Increment one integer counter by name."""
        value = self.summary.get(name)
        if isinstance(value, int):
            self.summary[name] = value + 1

    def record_frame(self) -> int:
        """Record and return a decoded frame's UTC receive timestamp."""
        received_timestamp = time.time_ns() // 1_000
        self.increment("websocket_frame_count")
        self.summary["last_websocket_frame_timestamp"] = received_timestamp
        return received_timestamp


def _fetch_channel_with_retry(
    downloader: KickChatDownloader,
    username: str,
    request: ChatRequest,
) -> JSONDict:
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
            return downloader._kick_client.fetch_channel(username)
        except (KickServerError, RequestException, OSError) as error:
            downloader.retry(attempt_number, error=error, request=request)
    msg = "unreachable: retry should have raised RetriesExceeded"
    raise RuntimeError(msg)


def _resolve_channel(
    data: JSONDict,
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

    channel_id, chatroom_id = str(raw_channel_id), str(raw_chatroom_id)
    if not is_numeric_id(channel_id) or not is_numeric_id(chatroom_id):
        msg = f"Kick channel {username!r} returned a non-numeric channel/chatroom id."
        raise KickError(msg)

    livestream = data.get("livestream")
    if not isinstance(livestream, dict):
        log("info", f'Kick channel "{username}" is offline; chatroom is still active.')
        title = username
    else:
        raw_title = livestream.get("session_title")
        title = str(raw_title) if raw_title else username

    return channel_id, chatroom_id, title


def _is_live_status(data: JSONDict) -> bool:
    """Return ``True`` if the channel metadata indicates a live stream."""
    livestream = data.get("livestream")
    return isinstance(livestream, dict)


def get_chat_by_channel(
    downloader: KickChatDownloader,
    username: str,
    request: ChatRequest,
    *,
    transport_factory: Callable[[], KickPusherTransport] | None = None,
    frame_iterator: Callable[[KickPusherTransport], Generator[JSONDict, None, None]]
    | None = None,
) -> Chat:
    """Build a live :class:`Chat` for a Kick channel.

    Works for both live and offline channels — the chatroom is always active.

    Args:
        downloader: The Kick downloader.
        username: Channel username/slug.
        request: The active chat request.
        transport_factory: Optional factory for the WebSocket transport
            (tests inject a fake).
        frame_iterator: Optional replacement for the live frame generator
            (tests inject a finite generator).

    Returns:
        A configured :class:`Chat` whose generator yields normalized messages.
    """
    if request.start_time is not None or request.end_time is not None:
        msg = (
            "Kick live chat does not support --start_time or --end_time. "
            "Use a Kick VOD URL to retrieve a bounded replay."
        )
        raise InvalidParameter(msg)

    data = _fetch_channel_with_retry(downloader, username, request)
    channel_id, chatroom_id, title = _resolve_channel(data, username)
    status = "live" if _is_live_status(data) else "idle"
    diagnostics = _KickLiveDiagnostics()

    return Chat(
        _iter_chat_messages(
            downloader,
            username,
            channel_id,
            chatroom_id,
            request,
            diagnostics,
            transport_factory=transport_factory,
            frame_iterator=frame_iterator,
        ),
        title=title,
        status=status,
        video_type="video",
        id=username,
        diagnostics=diagnostics.summary,
    )


def _resolve_ws_proxy(downloader: object) -> str | None:
    """Return the effective proxy URL for Kick's secure WebSocket."""
    return resolve_session_proxy(
        getattr(downloader, "session", None),
        "https://ws-us2.pusher.com",
    )


def _open_subscribed_transport(
    downloader: KickChatDownloader,
    chatroom_id: str,
    request: ChatRequest,
    transport_factory: Callable[[], KickPusherTransport],
    *,
    proxy_url: str | None = None,
    pusher_http_client: _HttpClient | None = None,
    force_discover: bool = False,
) -> KickPusherTransport:
    """Open a transport and subscribe to the chatroom, retrying failures.

    Args:
        downloader: The Kick downloader.
        chatroom_id: Chatroom id to subscribe to.
        request: The active chat request (retry policy and recv timeout).
        transport_factory: Factory producing a fresh transport.
        proxy_url: Optional HTTP, HTTPS, or SOCKS proxy URL.
        pusher_http_client: HTTP client used for Pusher-key discovery.
        force_discover: Whether to bypass the cached Pusher application key.

    Returns:
        A connected, subscribed transport.
    """
    for attempt_number in _attempt_numbers(request.max_attempts):
        transport = transport_factory()
        transport._proxy_url = proxy_url
        transport._pusher_http_client = pusher_http_client
        try:
            if force_discover:
                transport.connect(
                    downloader._http_timeout[0],
                    force_discover=True,
                )
            else:
                transport.connect(downloader._http_timeout[0])
            transport.subscribe(chatroom_id)
            effective_receive_timeout = max(
                request.message_receive_timeout,
                _MIN_RECEIVE_TIMEOUT_SECONDS,
            )
            log(
                "debug",
                "Kick WebSocket receive timeout: "
                f"requested={request.message_receive_timeout}s, "
                f"effective={effective_receive_timeout}s.",
            )
            transport.set_timeout(effective_receive_timeout)
        except ConnectionError as error:
            transport.close()
            try:
                downloader.retry(attempt_number, error=error, request=request)
            except RetriesExceeded as exhausted:
                msg = f"{exhausted} Last Kick WebSocket error: {error}"
                raise RetriesExceeded(msg) from error
        else:
            return transport
    msg = "unreachable: retry should have raised RetriesExceeded"
    raise RuntimeError(msg)


def _recover_pusher_transport(
    downloader: KickChatDownloader,
    transport: KickPusherTransport,
    chatroom_id: str,
    request: ChatRequest,
    transport_factory: Callable[[], KickPusherTransport],
    error: KickError,
    recovery_count: int,
    *,
    proxy_url: str | None,
    pusher_http_client: _HttpClient | None,
) -> KickPusherTransport:
    """Reconnect once with a freshly discovered Pusher application key."""
    transport.close()
    if recovery_count > 1:
        raise error
    wait_for_reconnect(
        recovery_count,
        error=error,
        request=request,
        provider="Kick Pusher protocol",
    )
    refreshed = _open_subscribed_transport(
        downloader,
        chatroom_id,
        request,
        transport_factory,
        proxy_url=proxy_url,
        pusher_http_client=pusher_http_client,
        force_discover=True,
    )
    log(
        "warning",
        "Kick Pusher rejected the cached application key; "
        "reconnected with a freshly discovered key.",
    )
    return refreshed


def _iter_preloaded_chat(
    downloader: KickChatDownloader,
    channel_id: str,
    username: str,
    emit: Callable[[JSONDict], bool],
) -> Generator[JSONDict, None, None]:
    """Yield recent HTTP history and current pin state without duplicates."""
    try:
        preloaded = downloader._kick_client.fetch_preloaded_chat_state(
            channel_id,
            username,
        )
    except (
        CaptchaChallengeRequired,
        KickError,
        RequestException,
        OSError,
    ) as error:
        logger.debug("Kick preloaded-message fetch failed (non-fatal): %s", error)
        return
    for message in reversed(parse_preloaded_messages(preloaded.messages)):
        if emit(message):
            yield message
    if preloaded.pinned_message is not None:
        try:
            pinned_message = parse_pinned_message_created_event(
                preloaded.pinned_message
            )
        except (ParsingError, ValueError, TypeError, KeyError, IndexError) as error:
            capture_debug_sample(
                "kick-malformed-preloaded-pin",
                {"raw": preloaded.pinned_message, "error": str(error)},
                sample_limit=KICK_DEBUG_SAMPLE_LIMIT,
            )
            logger.debug("Skipping malformed Kick startup pin: %s", error)
        else:
            if emit(pinned_message):
                yield pinned_message


def _iter_chat_messages(  # noqa: C901 — live reconnect and key-refresh paths are intrinsic to the stream loop
    downloader: KickChatDownloader,
    username: str,
    channel_id: str,
    chatroom_id: str,
    request: ChatRequest,
    diagnostics: _KickLiveDiagnostics,
    *,
    transport_factory: Callable[[], KickPusherTransport] | None = None,
    frame_iterator: Callable[[KickPusherTransport], Generator[JSONDict, None, None]]
    | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield normalized live chat messages for a channel."""
    if transport_factory is None:
        transport_factory = partial(
            KickPusherTransport,
            diagnostic_callback=diagnostics.increment,
        )
    frame_iterator = frame_iterator or read_frames

    msg_filter = MessageFilter.from_request(MESSAGE_GROUPS, request)
    seen_message_cache = _SeenMessageCache(limit=_KICK_LIVE_SEEN_MESSAGE_LIMIT)
    capture_successful_frames = (
        os.environ.get(_SUCCESSFUL_FRAME_CAPTURE_ENV, "").strip().lower()
        in _TRUTHY_ENV_VALUES
    )
    successful_frame_capture_attempts: dict[str, int] = {}

    def emit(message: dict[str, Any]) -> bool:
        message_id = message.get("message_id")
        if isinstance(message_id, str) and message_id:
            is_new, _evicted = seen_message_cache.register(message_id)
            if not is_new:
                return False
        return msg_filter.should_add(message)

    # 1. Preloaded history (best-effort; non-fatal on failure).
    yield from _iter_preloaded_chat(
        downloader,
        channel_id,
        username,
        emit,
    )

    # 2. Live WebSocket feed with reconnect.
    proxy_url = _resolve_ws_proxy(downloader)
    session = getattr(downloader, "session", None)
    pusher_http_client = (
        _RequestsHttpClient(
            session,
            configured_timeout=getattr(downloader, "_http_timeout", None),
        )
        if session is not None
        else None
    )
    transport = _open_subscribed_transport(
        downloader,
        chatroom_id,
        request,
        transport_factory,
        proxy_url=proxy_url,
        pusher_http_client=pusher_http_client,
    )
    consecutive_connection_failures = 0
    pusher_error_recoveries = 0
    try:
        while True:
            try:
                for frame in frame_iterator(transport):
                    # A decoded application frame proves this connection made
                    # progress, even when it is not a chat-message event.
                    consecutive_connection_failures = 0
                    received_timestamp = diagnostics.record_frame()
                    live_message = dispatch_event(
                        frame,
                        record_diagnostic=diagnostics.increment,
                    )
                    if live_message is not None:
                        if "timestamp" not in live_message:
                            live_message.setdefault(
                                "received_timestamp",
                                received_timestamp,
                            )
                        message_type = live_message.get("message_type")
                        capture_attempts = (
                            successful_frame_capture_attempts.get(message_type, 0)
                            if isinstance(message_type, str)
                            else 0
                        )
                        if (
                            capture_successful_frames
                            and isinstance(message_type, str)
                            and capture_attempts < _SUCCESSFUL_FRAME_CAPTURE_LIMIT
                        ):
                            successful_frame_capture_attempts[message_type] = (
                                capture_attempts + 1
                            )
                            capture_debug_sample(
                                "kick-websocket-frame-"
                                + message_type.replace("_", "-"),
                                frame,
                                sample_limit=_SUCCESSFUL_FRAME_CAPTURE_LIMIT,
                            )
                        pusher_error_recoveries = 0
                        if emit(live_message):
                            yield live_message
            except ConnectionError as error:
                logger.debug("Kick WebSocket disconnected; reconnecting: %s", error)
                transport.close()
                consecutive_connection_failures += 1
                wait_for_reconnect(
                    consecutive_connection_failures,
                    error=error,
                    request=request,
                    provider="Kick WebSocket",
                )
                transport = _open_subscribed_transport(
                    downloader,
                    chatroom_id,
                    request,
                    transport_factory,
                    proxy_url=proxy_url,
                    pusher_http_client=pusher_http_client,
                )
                diagnostics.increment("websocket_reconnect_count")
                log(
                    "debug",
                    "Kick WebSocket reconnected; checking recent history for "
                    "messages missed during the outage.",
                )
                yield from _iter_preloaded_chat(
                    downloader,
                    channel_id,
                    username,
                    emit,
                )
            except KickError as error:
                pusher_error_recoveries += 1
                transport = _recover_pusher_transport(
                    downloader,
                    transport,
                    chatroom_id,
                    request,
                    transport_factory,
                    error,
                    pusher_error_recoveries,
                    proxy_url=proxy_url,
                    pusher_http_client=pusher_http_client,
                )
                diagnostics.increment("pusher_key_recovery_count")
                yield from _iter_preloaded_chat(
                    downloader,
                    channel_id,
                    username,
                    emit,
                )
            else:
                break
    finally:
        transport.close()

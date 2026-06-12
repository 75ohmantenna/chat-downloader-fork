# SPDX-License-Identifier: MIT

"""Context construction helpers for the YouTube chat continuation loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.errors import NoContinuation
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.utils.time_utils import ensure_seconds

from .client_auth import _generate_sapisidhash_header
from .client_context import (
    _generate_headers,
    _get_innertube_context,
    apply_request_profile_to_innertube_context,
)
from .constants_message import _MESSAGE_GROUPS, _MESSAGE_TYPES
from .constants_patterns import _YT_HOME
from .continuation_loop import (
    ContinuationLoopState,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
    get_live_start_time_ms,
)
from .helpers import _safe_get_dict, require_innertube_api_key
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto

_MS_PER_SECOND = 1000


@dataclass
class _ChatContext:
    """Pre-loop state assembled once before the continuation loop begins."""

    continuation_url: str
    innertube_context: dict[str, Any]
    msg_filter: MessageFilter
    time_filter: TimeRangeFilter | None
    loop_state: ContinuationLoopState
    live_start_time_ms: int
    is_replay: bool
    offset: float | None


def _select_initial_continuation(
    initial_continuation_info: dict[str, str],
    *,
    chat_type: str,
    is_replay: bool,
) -> tuple[str, str]:
    """Select the initial continuation by semantic chat label."""
    if chat_type == "top":
        labels = ("Top chat replay", "Top chat") if is_replay else ("Top chat",)
    else:
        labels = ("Live chat replay", "Live chat") if is_replay else ("Live chat",)

    for label in labels:
        token = initial_continuation_info.get(label)
        if token:
            return label, token

    available = ", ".join(initial_continuation_info) or "none"
    msg = (
        f"Initial {chat_type} chat continuation could not be found. "
        f"Available continuation labels: {available}"
    )
    raise NoContinuation(msg)


def _profiled_innertube_context(
    ytcfg: dict[str, Any],
    profile_name: object,
) -> dict[str, Any]:
    """Return an Innertube context adjusted for the active request profile."""
    context = _get_innertube_context(ytcfg)
    return apply_request_profile_to_innertube_context(context, profile_name)


def _apply_live_timing(
    message: dict[str, Any],
    loop_state: ContinuationLoopState,
    live_start_time_ms: int,
) -> None:
    """Enrich a live message with player-offset timing and update loop state."""
    live_offset = derive_live_offset_milliseconds(message, live_start_time_ms)
    if live_offset is not None:
        enrich_live_message_timing(message, live_offset)
        loop_state.offset_milliseconds = live_offset


def _build_continuation_urls(
    continuation: str,
    api_key: str,
    *,
    is_replay: bool,
) -> tuple[str, str]:
    """Return (init_page, continuation_url) for the chat loop."""
    api_type = "live_chat_replay" if is_replay else "live_chat"
    init_page = f"{_YT_HOME}/{api_type}?continuation={continuation}"
    continuation_url = f"{_YT_HOME}/youtubei/v1/live_chat/get_{api_type}?key={api_key}"
    return init_page, continuation_url


def _build_message_filters(
    params: ChatRequest,
    messages_types_to_add: list[str],
    *,
    is_replay: bool,
    start_time: float | None,
    end_time: float | None,
    offset: float | None,
) -> tuple[MessageFilter, TimeRangeFilter | None]:
    """Assemble the message-group filter and optional replay time filter."""
    messages_groups_to_add = (
        []
        if messages_types_to_add
        else (params.message_groups if isinstance(params.message_groups, list) else [])
    )
    msg_filter = MessageFilter(
        _MESSAGE_GROUPS,
        messages_groups_to_add or None,
        messages_types_to_add,
    )
    time_filter = (
        TimeRangeFilter(
            start_time,
            end_time,
            offset=offset,
            skip_mode="first_page",
        )
        if is_replay
        else None
    )
    return msg_filter, time_filter


def _apply_session_headers(
    self: YouTubeDownloaderProto,
    ytcfg: dict[str, Any],
    init_page: str,
) -> None:
    """Install the InnerTube auth and content-type headers on the session."""
    self.update_session_headers(
        _generate_headers(ytcfg, self, _YT_HOME, _generate_sapisidhash_header),
    )
    self.update_session_headers(
        {"content-type": "application/json", "referer": init_page},
    )


def _build_chat_context(
    self: YouTubeDownloaderProto,
    initial_info: dict[str, Any],
    ytcfg: dict[str, Any],
    params: ChatRequest,
) -> _ChatContext:
    """Assemble all pre-loop state from *initial_info*, *ytcfg*, and *params*.

    Validates message groups and types, updates session headers, and returns a
    :class:`_ChatContext` that the main loop can use without touching *self*
    again during setup.

    Raises:
        NoContinuation: When the requested chat type index is absent.
        InvalidParameter: When an unknown message group is requested.
    """
    initial_continuation_info = _safe_get_dict(initial_info, "continuation_info")

    status = initial_info.get("status")
    offset = initial_info.get("offset")  # Clips

    start_time = ensure_seconds(params.start_time)
    end_time = ensure_seconds(params.end_time)

    is_replay = status in REPLAY_STATUSES
    chat_type = params.chat_type
    continuation_label, continuation = _select_initial_continuation(
        initial_continuation_info,
        chat_type=chat_type,
        is_replay=is_replay,
    )
    log("debug", f"Getting {chat_type.title()} chat ({continuation_label}).")

    api_key = require_innertube_api_key(ytcfg)
    init_page, continuation_url = _build_continuation_urls(
        continuation, api_key, is_replay=is_replay
    )

    messages_types_to_add = params.message_types or []
    self.check_for_invalid_types(messages_types_to_add, _MESSAGE_TYPES)
    msg_filter, time_filter = _build_message_filters(
        params,
        messages_types_to_add,
        is_replay=is_replay,
        start_time=start_time,
        end_time=end_time,
        offset=offset,
    )

    _apply_session_headers(self, ytcfg, init_page)

    innertube_context = _profiled_innertube_context(
        ytcfg,
        getattr(self, "_request_profile", None),
    )
    offset_milliseconds = (
        start_time * _MS_PER_SECOND if isinstance(start_time, (float, int)) else None
    )
    loop_state = ContinuationLoopState(
        continuation=continuation,
        offset_milliseconds=offset_milliseconds,
    )
    live_start_time_ms = get_live_start_time_ms()

    return _ChatContext(
        continuation_url=continuation_url,
        innertube_context=innertube_context,
        msg_filter=msg_filter,
        time_filter=time_filter,
        loop_state=loop_state,
        live_start_time_ms=live_start_time_ms,
        is_replay=is_replay,
        offset=offset,
    )

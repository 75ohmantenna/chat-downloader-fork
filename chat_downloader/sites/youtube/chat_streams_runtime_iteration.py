# SPDX-License-Identifier: MIT

"""Chat continuation iteration logic for YouTube."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, SupportsIndex, SupportsInt

from chat_downloader.debugging import capture_debug_sample, debug_log, log
from chat_downloader.errors import (
    ChatDownloaderError,
    IncompleteContinuationError,
    NoChatReplay,
    NoContinuation,
)
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.time_utils import ensure_seconds
from chat_downloader.utils.timed_utils import polling_sleep

from .client_auth import _generate_sapisidhash_header
from .client_context import (
    _generate_headers,
    _get_innertube_context,
    apply_request_profile_to_innertube_context,
)
from .client_requests_continuation import _get_continuation_info
from .constants_message import _MESSAGE_GROUPS, _MESSAGE_TYPES
from .constants_patterns import _YT_HOME
from .continuation_loop import (
    ContinuationLoopState,
    build_continuation_params,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
    extract_visitor_data,
    get_live_start_time_ms,
    update_state_from_result,
)
from .continuations import (
    parse_continuation_response,
    summarize_continuation_payload,
)
from .helpers import _safe_get_dict
from .message_pipeline import process_pipeline_action
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

_MS_PER_SECOND = 1000
_YOUTUBE_POLL_DELAY_FALLBACK_MS = 5000
_YOUTUBE_POLL_DELAY_MIN_MS = 500
_YOUTUBE_POLL_DELAY_MAX_MS = 8000
_PollDelayHint = str | bytes | bytearray | SupportsInt | SupportsIndex | None


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


def _raise_if_api_error(yt_info: dict[str, Any]) -> None:
    """Raise a typed exception when the YouTube API returns an error
    payload.
    """
    if "error" not in yt_info:
        return

    error_info = yt_info.get("error")
    if isinstance(error_info, dict):
        error_message = error_info.get("message", "Unknown error")
        error_code = error_info.get("code", "")
    else:
        error_message = str(error_info) if error_info else "Unknown error"
        error_code = ""
    log("debug", f"API error response: {error_info}")

    if str(error_code) == "400":
        msg = f"Chat replay is not available for this video. {error_message}"
        raise NoChatReplay(
            msg,
        )
    msg = f"YouTube API error ({error_code}): {error_message}"
    raise ChatDownloaderError(msg)


def _log_continuation_debug_info(cont_result: Any) -> None:
    """Log parsed continuation details without cluttering the main loop."""
    if not cont_result.debug_info:
        return

    cont_debug = cont_result.debug_info
    if cont_debug.get("unknown"):
        cont_key = cont_debug.get("continuation_key")
        cont_entry = cont_debug.get("continuation_entry", {})
        payload_summary = cont_debug.get("payload_summary")
        capture_debug_sample(
            f"youtube-unknown-continuation-{cont_key or 'unknown'}",
            {
                "continuation_key": cont_key,
                "continuation_entry": cont_entry,
                "payload_summary": payload_summary,
            },
        )
        debug_log(
            f"Unknown continuation: {cont_key}",
            {cont_key: cont_entry},
            {"payload_summary": payload_summary},
        )
        return

    log(
        "debug",
        f"Continuation info: {cont_debug.get('continuation_entry')}",
    )


def _attempt_profile_fallback(self: YouTubeDownloaderProto) -> bool:
    """Try switching to the next YouTube request profile after an incomplete
    response.

    Returns True if a new profile was applied (caller should retry). Returns
    False if fallback is disabled or no next profile is available (caller
    should re-raise the original exception).
    """
    if not self._auto_profile_fallback:
        return False
    next_profile = get_next_request_profile(
        self._request_profile,
        site="youtube",
    )
    if next_profile is None or not self.apply_request_profile(next_profile):
        return False
    log(
        "warning",
        "Switching YouTube request profile after repeated incomplete "
        f"continuation responses: {next_profile}",
    )
    return True


def _profiled_innertube_context(
    ytcfg: dict[str, Any],
    profile_name: object,
) -> dict[str, Any]:
    """Return an Innertube context adjusted for the active request profile."""
    context = _get_innertube_context(ytcfg)
    return apply_request_profile_to_innertube_context(context, profile_name)


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
        labels = (
            ("Live chat replay", "Live chat") if is_replay else ("Live chat",)
        )

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


def _update_visitor_data(
    self: YouTubeDownloaderProto, yt_info: dict[str, Any]
) -> None:
    """Propagate visitor-data from the response into session headers."""
    visitor_data = extract_visitor_data(yt_info)
    if visitor_data:
        self.update_session_headers({"x-goog-visitor-id": visitor_data})
        log("debug", f"Updated visitor data: {visitor_data}")


def _apply_response_state_updates(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    auth: str | None = None,
) -> None:
    """Apply response-driven session/header state updates in one place."""
    _update_visitor_data(self, yt_info)
    if auth:
        self.update_session_headers({"authorization": auth})


def _handle_continuation_response(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    ytcfg: dict[str, Any],
    continuation_params: dict[str, Any],
) -> None:
    """Apply response-driven state, log request context, and raise API
    errors.
    """
    auth = _generate_sapisidhash_header(self, _YT_HOME, ytcfg)
    _apply_response_state_updates(self, yt_info, auth)
    _log_request_context(self, yt_info, continuation_params)
    _raise_if_api_error(yt_info)


def _log_request_context(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    continuation_params: dict[str, Any],
) -> None:
    """Log continuation parameters, session headers, and login state."""
    debug_info = {
        "click_tracking": multi_get(
            continuation_params,
            "context",
            "clickTracking",
        ),
        "continuation": multi_get(continuation_params, "continuation"),
    }
    log(
        "debug",
        [
            f"Continuation parameters: {debug_info}",
            f"Session headers: {', '.join(self.session.headers.keys())}",
        ],
    )

    logged_in_info = multi_get(
        yt_info,
        "responseContext",
        "serviceTrackingParams",
        1,
        "params",
        0,
    )
    log("debug", f"Logged-in info: {logged_in_info}")


def _apply_live_timing(
    message: dict[str, Any],
    loop_state: Any,
    live_start_time_ms: int,
) -> None:
    """Enrich a live message with player-offset timing and update loop
    state.
    """
    live_offset = derive_live_offset_milliseconds(message, live_start_time_ms)
    if live_offset is not None:
        enrich_live_message_timing(message, live_offset)
        loop_state.offset_milliseconds = live_offset


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
    initial_continuation_info = _safe_get_dict(
        initial_info, "continuation_info"
    )

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

    api_type = "live_chat"
    if is_replay:
        api_type += "_replay"

    init_page = f"{_YT_HOME}/{api_type}?continuation={continuation}"
    api_key = ytcfg.get("INNERTUBE_API_KEY")
    continuation_url = (
        f"{_YT_HOME}/youtubei/v1/live_chat/get_{api_type}?key={api_key}"
    )
    offset_milliseconds = (
        start_time * _MS_PER_SECOND
        if isinstance(start_time, (float, int))
        else None
    )

    messages_types_to_add = params.message_types or []
    messages_groups_to_add = (
        []
        if messages_types_to_add
        else (
            params.message_groups
            if isinstance(params.message_groups, list)
            else []
        )
    )
    self.check_for_invalid_types(messages_types_to_add, _MESSAGE_TYPES)

    msg_filter = MessageFilter(
        _MESSAGE_GROUPS,
        messages_groups_to_add or None,
        messages_types_to_add,
    )

    self.update_session_headers(
        _generate_headers(ytcfg, self, _YT_HOME, _generate_sapisidhash_header),
    )
    self.update_session_headers(
        {"content-type": "application/json", "referer": init_page},
    )

    innertube_context = _profiled_innertube_context(
        ytcfg,
        getattr(self, "_request_profile", None),
    )
    time_filter = (
        TimeRangeFilter(
            start_time,
            end_time,
            offset=offset,
            skip_mode="first_page" if is_replay else "none",
        )
        if is_replay
        else None
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


def _process_actions(
    actions: list[dict[str, Any]],
    offset: float | None,
    msg_filter: MessageFilter,
    time_filter: TimeRangeFilter | None,
    loop_state: ContinuationLoopState,
    live_start_time_ms: int,
    is_replay: bool,
) -> Generator[dict[str, Any], None, bool]:
    """Walk *actions*, apply filters, and yield accepted messages.

    Updates *loop_state.offset_milliseconds* in-place when a live message
    carries a usable timestamp.

    Args:
        actions: Raw action dicts from the ``liveChatContinuation`` payload.
        offset: Clip or replay time offset in seconds (passed to the pipeline).
        msg_filter: Message-type inclusion filter.
        time_filter: Optional time-range filter for replay captures.
        loop_state: Mutable loop state; ``offset_milliseconds`` may be updated.
        live_start_time_ms: Baseline epoch-ms for live offset calculation.
        is_replay: ``True`` for replay streams; suppresses live-timing
            enrichment.

    Yields:
        Accepted message dicts, one per qualifying action.

    Returns:
        ``True`` if a ``"stop"`` disposition was encountered (caller should
        terminate the outer loop), ``False`` otherwise.
    """
    message_count = 0
    for action in actions:
        pipeline_result = process_pipeline_action(
            action,
            offset or 0.0,
            msg_filter,
            time_filter,
        )
        if pipeline_result.disposition == "skip":
            continue
        if pipeline_result.disposition == "stop":
            return True
        if not is_replay and pipeline_result.message is not None:
            _apply_live_timing(
                pipeline_result.message, loop_state, live_start_time_ms
            )
        message_count += 1
        if pipeline_result.message is not None:
            yield pipeline_result.message

    log("debug", f"Total number of messages: {message_count}")
    return False


def _resolve_poll_delay_ms(timeout_ms: _PollDelayHint) -> int:
    """Return the safe YouTube chat poll delay in milliseconds."""
    if isinstance(timeout_ms, bool) or timeout_ms is None:
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    try:
        poll_delay_ms = int(timeout_ms)
    except (TypeError, ValueError):
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    if poll_delay_ms < 0:
        return _YOUTUBE_POLL_DELAY_FALLBACK_MS
    return max(
        _YOUTUBE_POLL_DELAY_MIN_MS,
        min(poll_delay_ms, _YOUTUBE_POLL_DELAY_MAX_MS),
    )


def _advance_continuation_loop(
    ctx: _ChatContext,
    yt_info: dict[str, Any],
) -> bool:
    """Advance continuation state and return True when iteration should
    stop.
    """
    cont_result = parse_continuation_response(yt_info)
    _log_continuation_debug_info(cont_result)
    ctx.loop_state = update_state_from_result(ctx.loop_state, cont_result)

    poll_delay_ms = _resolve_poll_delay_ms(cont_result.timeout_ms)
    log("debug", f"Sleeping for {poll_delay_ms}ms.")
    polling_sleep(poll_delay_ms / _MS_PER_SECOND)

    if ctx.time_filter is not None:
        ctx.time_filter.end_page()
    return bool(cont_result.is_end)


def _get_chat_messages(
    self: YouTubeDownloaderProto,
    initial_info: dict[str, Any],
    ytcfg: dict[str, Any],
    params: ChatRequest,
) -> Generator[dict[str, Any], None, None]:
    """Generator that yields chat messages from a YouTube continuation
    endpoint.
    """
    ctx = _build_chat_context(self, initial_info, ytcfg, params)
    ended_cleanly = False
    # Defensive bounds: live chat normally produces actions or rotates the
    # continuation token within seconds. If neither happens for several
    # polls in a row, YouTube has effectively stopped serving us.
    max_no_progress_polls = 5
    max_profile_fallbacks = 3
    no_progress_count = 0
    fallback_count = 0

    while True:
        continuation_params = build_continuation_params(
            ctx.innertube_context,
            ctx.loop_state,
            ctx.is_replay,
        )
        token_before_request = ctx.loop_state.continuation

        try:
            yt_info = _get_continuation_info(
                ctx.continuation_url,
                self._session_post,
                params,
                json=continuation_params,
            )
        except IncompleteContinuationError:
            fallback_count += 1
            if fallback_count > max_profile_fallbacks:
                log(
                    "warning",
                    "Exhausted profile fallbacks "
                    f"({max_profile_fallbacks}) for incomplete continuation "
                    "responses; surfacing the underlying error.",
                )
                raise
            if not _attempt_profile_fallback(self):
                raise
            ctx.innertube_context = _profiled_innertube_context(
                ytcfg,
                getattr(self, "_request_profile", None),
            )
            continue

        _handle_continuation_response(self, yt_info, ytcfg, continuation_params)

        info = multi_get(
            yt_info, "continuationContents", "liveChatContinuation"
        )
        if not info:
            summary = summarize_continuation_payload(yt_info)
            msg = (
                "Missing continuationContents.liveChatContinuation in "
                "response body. "
                f"Summary: {summary}"
            )
            raise IncompleteContinuationError(msg)

        actions = info.get("actions") or []
        if actions:
            stop_requested: bool = yield from _process_actions(
                actions,
                ctx.offset,
                ctx.msg_filter,
                ctx.time_filter,
                ctx.loop_state,
                ctx.live_start_time_ms,
                ctx.is_replay,
            )
            if stop_requested:
                return
        elif ctx.is_replay:
            break
        else:
            log("debug", "No actions to process.")

        if _advance_continuation_loop(ctx, yt_info):
            ended_cleanly = True
            break

        # No-progress guard for live chat: zero actions AND the token
        # didn't rotate means YouTube has effectively stopped advancing us.
        if not actions and ctx.loop_state.continuation == token_before_request:
            no_progress_count += 1
            if no_progress_count >= max_no_progress_polls:
                msg = (
                    "No progress on YouTube continuation: "
                    f"{max_no_progress_polls} consecutive empty polls with "
                    "an unchanged continuation token. The live chat may "
                    "have ended without a terminator, or the token is "
                    "stale."
                )
                raise NoContinuation(msg)
        else:
            no_progress_count = 0

    if ended_cleanly:
        end_msg: dict[str, Any] = {
            "message_type": "chat_ended",
            "action_type": "chat_ended",
            "message": None,
        }
        if ctx.msg_filter.should_add(end_msg):
            yield end_msg

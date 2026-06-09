# SPDX-License-Identifier: MIT

"""Chat continuation loop for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, SupportsIndex, SupportsInt

from chat_downloader.debugging import log
from chat_downloader.errors import IncompleteContinuationError, NoContinuation
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.timed_utils import polling_sleep

from .chat_streams_context import (
    _apply_live_timing,
    _build_chat_context,
    _ChatContext,
    _profiled_innertube_context,
)
from .chat_streams_response import (
    _handle_continuation_response,
    _log_continuation_debug_info,
)
from .client_requests_continuation import _get_continuation_info
from .constants_patterns import (
    _YT_MAX_NO_PROGRESS_POLLS,
    _YT_MAX_PROFILE_FALLBACKS,
)
from .continuation_loop import (
    ContinuationLoopState,
    build_continuation_params,
    update_state_from_result,
)
from .continuations import (
    parse_continuation_response,
    summarize_continuation_payload,
)
from .message_pipeline import process_pipeline_action

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
    from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto

_MS_PER_SECOND = 1000
_YOUTUBE_POLL_DELAY_FALLBACK_MS = 5000
_YOUTUBE_POLL_DELAY_MIN_MS = 500
_YOUTUBE_POLL_DELAY_MAX_MS = 8000
_PollDelayHint = str | bytes | bytearray | SupportsInt | SupportsIndex | None


def _attempt_profile_fallback(self: YouTubeDownloaderProto) -> bool:
    """Try switching to the next YouTube request profile on incomplete data.

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
    """Advance continuation state and return True when iteration should stop."""
    cont_result = parse_continuation_response(yt_info)
    _log_continuation_debug_info(cont_result)
    ctx.loop_state = update_state_from_result(ctx.loop_state, cont_result)

    poll_delay_ms = _resolve_poll_delay_ms(cont_result.timeout_ms)
    log("debug", f"Sleeping for {poll_delay_ms}ms.")
    polling_sleep(poll_delay_ms / _MS_PER_SECOND)

    if ctx.time_filter is not None:
        ctx.time_filter.end_page()
    return bool(cont_result.is_end)


def _get_chat_messages(  # noqa: C901 — YouTube chat continuation loop handles many recovery paths
    self: YouTubeDownloaderProto,
    initial_info: dict[str, Any],
    ytcfg: dict[str, Any],
    params: ChatRequest,
) -> Generator[dict[str, Any], None, None]:
    """Yield chat messages from a YouTube continuation endpoint."""
    ctx = _build_chat_context(self, initial_info, ytcfg, params)
    ended_cleanly = False
    # Defensive bounds: live chat normally produces actions or rotates the
    # continuation token within seconds. If neither happens for several
    # polls in a row, YouTube has effectively stopped serving us.
    max_no_progress_polls = _YT_MAX_NO_PROGRESS_POLLS
    max_profile_fallbacks = _YT_MAX_PROFILE_FALLBACKS
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

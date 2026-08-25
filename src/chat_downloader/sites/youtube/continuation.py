# SPDX-License-Identifier: MIT

"""YouTube chat continuation loop.

A single cohesive unit: :class:`_ContinuationLoop` owns the per-run state
(downloader, context, progress) and drives setup, request/response handling, and
iteration as methods. The genuinely pure helpers it composes live in
:mod:`.continuation_helpers` and :mod:`.continuations`; the only functions kept
at module scope here are the stateless ones that carry no downloader dependency
(so they remain independently testable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log, log
from chat_downloader.errors import (
    ChatDownloaderError,
    IncompleteContinuationError,
    NoChatReplay,
    NoContinuation,
)
from chat_downloader.redaction import capture_debug_sample
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.sites.common import check_for_invalid_types
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict
from chat_downloader.utils.time_utils import ensure_seconds
from chat_downloader.utils.timed_generator import polling_sleep

from .client_auth import _generate_sapisidhash_header
from .client_context import (
    _generate_headers,
    _get_innertube_context,
    apply_request_profile_to_innertube_context,
    apply_request_profile_to_ytcfg,
)
from .client_requests_continuation import _get_continuation_info
from .constants_message import _MESSAGE_TYPES
from .constants_patterns import (
    _YT_HOME,
    _YT_MAX_NO_PROGRESS_POLLS,
    _YT_MAX_PROFILE_FALLBACKS,
)
from .continuation_helpers import (
    ContinuationLoopState,
    _build_continuation_urls,
    _build_message_filters,
    _resolve_poll_delay_ms,
    _select_initial_continuation,
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
from .helpers import require_innertube_api_key
from .message_pipeline import process_pipeline_action
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
    from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto
    from chat_downloader.utils.json_types import JSONDict

    from .continuations import ContinuationParseResult

_MS_PER_SECOND = 1000


@dataclass
class _ChatContext:
    """Pre-loop state assembled once before the continuation loop begins."""

    continuation_url: str
    innertube_context: JSONDict
    msg_filter: MessageFilter
    time_filter: TimeRangeFilter | None
    loop_state: ContinuationLoopState
    live_start_time_ms: int
    is_replay: bool
    offset: float | None


@dataclass(slots=True)
class _ContinuationProgress:
    """Tracks empty-poll and profile-fallback streaks for the chat loop."""

    max_no_progress_polls: int
    max_profile_fallbacks: int
    no_progress_count: int = field(default=0)
    fallback_count: int = field(default=0)

    def register_fallback(self) -> bool:
        """Count an incomplete-continuation fallback; True if exhausted."""
        self.fallback_count += 1
        return self.fallback_count > self.max_profile_fallbacks

    def register_poll(self, *, made_progress: bool) -> bool:
        """Track the empty-poll streak; True if the no-progress ceiling hit."""
        if made_progress:
            self.no_progress_count = 0
            return False
        self.no_progress_count += 1
        return self.no_progress_count >= self.max_no_progress_polls


# ---------------------------------------------------------------------------
# Stateless helpers (no downloader dependency) — kept at module scope so they
# stay independently unit-testable.
# ---------------------------------------------------------------------------


def _profiled_innertube_context(
    ytcfg: JSONDict,
    profile_name: object,
) -> JSONDict:
    """Return an Innertube context adjusted for the active request profile."""
    context = _get_innertube_context(ytcfg)
    return apply_request_profile_to_innertube_context(context, profile_name)


def _apply_live_timing(
    message: JSONDict,
    loop_state: ContinuationLoopState,
    live_start_time_ms: int,
) -> None:
    """Add signed presentation timing and advance nonnegative polling state."""
    live_offset = derive_live_offset_milliseconds(message, live_start_time_ms)
    if live_offset is not None:
        enrich_live_message_timing(message, live_offset)
        current_poll_offset = loop_state.offset_milliseconds or 0
        loop_state.offset_milliseconds = max(current_poll_offset, live_offset, 0)


def _raise_if_api_error(yt_info: JSONDict) -> None:
    """Raise a typed exception when the YouTube API returns an error payload."""
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


def _log_continuation_debug_info(cont_result: ContinuationParseResult) -> None:
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


def _process_actions(
    actions: list[JSONDict],
    offset: float | None,
    msg_filter: MessageFilter,
    time_filter: TimeRangeFilter | None,
    loop_state: ContinuationLoopState,
    live_start_time_ms: int,
    *,
    is_replay: bool,
) -> Generator[JSONDict, None, bool]:
    """Walk *actions*, apply filters, and yield accepted messages.

    Advances the nonnegative *loop_state.offset_milliseconds* in-place when a
    live message carries a usable timestamp. Presentation timing remains signed
    so initial-backlog messages retain their capture-relative ordering.

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
    processed_action_count = 0
    emitted_message_count = 0
    for action in actions:
        pipeline_result = process_pipeline_action(
            action,
            offset or 0.0,
            msg_filter,
            time_filter,
        )
        processed_action_count += 1
        if pipeline_result.disposition == "skip":
            continue
        if pipeline_result.disposition == "stop":
            return True
        if not is_replay and pipeline_result.message is not None:
            _apply_live_timing(pipeline_result.message, loop_state, live_start_time_ms)
        if pipeline_result.message is not None:
            emitted_message_count += 1
            yield pipeline_result.message

    log(
        "debug",
        "Processed actions in poll: "
        f"{processed_action_count}; emitted messages: {emitted_message_count}",
    )
    return False


def _advance_continuation_loop(
    ctx: _ChatContext,
    yt_info: JSONDict,
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


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


class _ContinuationLoop:
    """Owns one chat-retrieval run: setup, request/response, and iteration.

    Methods that touch the downloader/session use ``self.downloader`` directly,
    so there is no free-function ``self``-threading and no wide structural
    protocol to keep in sync.
    """

    def __init__(
        self,
        downloader: YouTubeDownloaderProto,
        initial_info: dict[str, Any],
        ytcfg: JSONDict,
        params: ChatRequest,
    ) -> None:
        self.downloader = downloader
        self.initial_info = initial_info
        self.ytcfg = ytcfg
        self.params = params
        self.ctx: _ChatContext
        self.progress = _ContinuationProgress(
            _YT_MAX_NO_PROGRESS_POLLS, _YT_MAX_PROFILE_FALLBACKS
        )

    # -- setup --------------------------------------------------------------

    def _apply_session_headers(self, init_page: str) -> None:
        """Install the InnerTube auth and content-type headers on the session."""
        self.downloader.update_session_headers(
            _generate_headers(
                self.ytcfg, self.downloader, _YT_HOME, _generate_sapisidhash_header
            ),
        )
        self.downloader.update_session_headers(
            {"content-type": "application/json", "referer": init_page},
        )

    def _build_context(self) -> _ChatContext:
        """Assemble all pre-loop state from the downloader inputs.

        Validates message groups and types, updates session headers, and returns
        a :class:`_ChatContext` the main loop uses without touching the
        downloader again during setup.

        Raises:
            NoContinuation: When the requested chat type index is absent.
            InvalidParameter: When an unknown message group is requested.
        """
        initial_info = self.initial_info
        self.ytcfg = apply_request_profile_to_ytcfg(
            self.ytcfg,
            getattr(self.downloader, "_request_profile", None),
        )
        ytcfg = self.ytcfg
        params = self.params

        initial_continuation_info = get_dict(initial_info, "continuation_info")

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
        check_for_invalid_types(messages_types_to_add, _MESSAGE_TYPES)
        msg_filter, time_filter = _build_message_filters(
            params,
            is_replay=is_replay,
            start_time=start_time,
            end_time=end_time,
            offset=offset,
        )

        self._apply_session_headers(init_page)

        innertube_context = _profiled_innertube_context(
            ytcfg,
            getattr(self.downloader, "_request_profile", None),
        )
        offset_milliseconds = (
            start_time * _MS_PER_SECOND
            if isinstance(start_time, (float, int))
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

    # -- response handling --------------------------------------------------

    def _update_visitor_data(self, yt_info: JSONDict) -> None:
        """Propagate visitor-data from the response into session headers."""
        visitor_data = extract_visitor_data(yt_info)
        if visitor_data:
            self.downloader.update_session_headers({"x-goog-visitor-id": visitor_data})
            log("debug", f"Updated visitor data: {visitor_data}")

    def _apply_response_state_updates(
        self, yt_info: JSONDict, auth: str | None = None
    ) -> None:
        """Apply response-driven session/header state updates in one place."""
        self._update_visitor_data(yt_info)
        if auth:
            self.downloader.update_session_headers({"authorization": auth})

    def _log_request_context(
        self, yt_info: JSONDict, continuation_params: JSONDict
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
                f"Session headers: {', '.join(self.downloader.session.headers.keys())}",
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

    def _handle_continuation_response(
        self, yt_info: JSONDict, continuation_params: JSONDict
    ) -> None:
        """Apply response-driven state, log request context, and raise errors."""
        auth = _generate_sapisidhash_header(self.downloader, _YT_HOME, self.ytcfg)
        self._apply_response_state_updates(yt_info, auth)
        self._log_request_context(yt_info, continuation_params)
        _raise_if_api_error(yt_info)

    # -- profile fallback ---------------------------------------------------

    def _attempt_profile_fallback(self) -> bool:
        """Try switching to the next YouTube request profile on incomplete data.

        Returns True if a new profile was applied (caller should retry). Returns
        False if fallback is disabled or no next profile is available (caller
        should re-raise the original exception).
        """
        downloader = self.downloader
        if not downloader._auto_profile_fallback:
            return False
        next_profile = get_next_request_profile(
            downloader._request_profile,
            site="youtube",
        )
        if next_profile is None or not downloader.apply_request_profile(next_profile):
            return False
        log(
            "warning",
            "Switching YouTube request profile after repeated incomplete "
            f"continuation responses: {next_profile}",
        )
        return True

    def _recover_incomplete_continuation(self) -> bool:
        """Try profile fallback after an incomplete continuation.

        Returns True if the loop should retry; False means the caller should
        re-raise the active IncompleteContinuationError.
        """
        previous_profile = getattr(self.downloader, "_request_profile", None)
        if self.progress.register_fallback():
            log(
                "warning",
                "Exhausted profile fallbacks "
                f"({self.progress.max_profile_fallbacks}) for incomplete "
                "continuation responses; surfacing the underlying error.",
            )
            return False
        if not self._attempt_profile_fallback():
            return False
        active_profile = getattr(self.downloader, "_request_profile", None)
        self.ytcfg = apply_request_profile_to_ytcfg(
            self.ytcfg,
            active_profile,
        )
        self.ctx.innertube_context = _profiled_innertube_context(
            self.ytcfg, active_profile
        )
        if active_profile == previous_profile:
            return True
        self.downloader.update_session_headers(
            _generate_headers(
                self.ytcfg,
                self.downloader,
                _YT_HOME,
                _generate_sapisidhash_header,
            )
        )
        return True

    # -- main loop ----------------------------------------------------------

    def run(  # noqa: C901 — live/replay branching, no-progress guard, and end-message injection are intrinsic to the continuation loop
        self,
    ) -> Generator[JSONDict, None, None]:
        """Yield chat messages from a YouTube continuation endpoint."""
        self.ctx = self._build_context()
        ctx = self.ctx
        ended_cleanly = False

        while True:
            continuation_params = build_continuation_params(
                ctx.innertube_context,
                ctx.loop_state,
                is_replay=ctx.is_replay,
            )
            token_before_request = ctx.loop_state.continuation

            try:
                yt_info = _get_continuation_info(
                    ctx.continuation_url,
                    self.downloader._session_post,
                    self.params,
                    json=continuation_params,
                )
            except IncompleteContinuationError:
                if not self._recover_incomplete_continuation():
                    raise
                continue

            self._handle_continuation_response(yt_info, continuation_params)

            info = multi_get(yt_info, "continuationContents", "liveChatContinuation")
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
                    is_replay=ctx.is_replay,
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

            made_progress = (
                bool(actions) or ctx.loop_state.continuation != token_before_request
            )
            if self.progress.register_poll(made_progress=made_progress):
                msg = (
                    "No progress on YouTube continuation: "
                    f"{self.progress.max_no_progress_polls} consecutive empty "
                    "polls with an unchanged continuation token. The live chat "
                    "may have ended without a terminator, or the token is stale."
                )
                raise NoContinuation(msg)

        if ended_cleanly:
            end_msg: JSONDict = {
                "message_type": "chat_ended",
                "action_type": "chat_ended",
                "message": None,
            }
            if ctx.msg_filter.should_add(end_msg):
                yield end_msg


def _get_chat_messages(
    downloader: YouTubeDownloaderProto,
    initial_info: dict[str, Any],
    ytcfg: JSONDict,
    params: ChatRequest,
) -> Generator[JSONDict, None, None]:
    """Yield chat messages from a YouTube continuation endpoint."""
    return _ContinuationLoop(downloader, initial_info, ytcfg, params).run()

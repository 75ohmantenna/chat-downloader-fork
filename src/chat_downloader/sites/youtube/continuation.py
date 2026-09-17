# SPDX-License-Identifier: MIT

"""Run YouTube continuation requests and message iteration.

Per-run downloader/context/progress state, setup, request/response handling,
and iteration belong to _ContinuationLoop.
Pure helpers live in .continuation_helpers and .continuations; module-level
functions here are stateless and downloader-independent for isolated testing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import debug_log, log
from chat_downloader.errors import (
    ChatDownloaderError,
    IncompleteContinuationError,
    NoChatReplay,
    NoContinuation,
)
from chat_downloader.redaction import BoundedSampleCapture, capture_debug_sample
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.sites.common import check_for_invalid_types
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_str
from chat_downloader.utils.time_utils import ensure_seconds
from chat_downloader.utils.timed_generator import polling_sleep

from .client_auth import _generate_sapisidhash_header
from .client_context import (
    _MANAGED_API_HEADER_NAMES,
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
    YOUTUBE_DEBUG_SAMPLE_LIMIT,
)
from .continuation_helpers import (
    ContinuationLoopState,
    _build_continuation_urls,
    _build_message_filters,
    _resolve_poll_delay_ms,
    _select_initial_continuation,
    build_continuation_params,
    extract_visitor_data,
    get_live_start_time_ms,
    update_state_from_result,
)
from .continuations import (
    parse_continuation_response,
    summarize_continuation_payload,
)
from .helpers import require_innertube_api_key
from .message_pipeline import _process_actions
from .paid_events import PaidEventCache
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
    from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto
    from chat_downloader.utils.json_types import JSONDict

    from .continuations import ContinuationParseResult

_MS_PER_SECOND = 1000
_SUCCESSFUL_RESPONSE_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES"
_SUCCESSFUL_RESPONSE_CAPTURE_LIMIT = 3


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
    replay_poll_interval: float | None


@dataclass(slots=True)
class _ContinuationProgress:
    """Tracks empty-poll and profile-fallback streaks for the chat loop."""

    max_no_progress_polls: int
    max_profile_fallbacks: int
    no_progress_count: int = field(default=0)
    fallback_count: int = field(default=0)
    latest_replay_offset_milliseconds: float | None = field(default=None)

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

    def response_advanced(
        self,
        actions: list[JSONDict],
        *,
        token_changed: bool,
        is_replay: bool,
    ) -> bool:
        """Return whether a response advanced its token or replay position."""
        if not is_replay:
            return bool(actions) or token_changed

        latest_offset = _latest_replay_action_offset_milliseconds(actions)
        offset_advanced = latest_offset is not None and (
            self.latest_replay_offset_milliseconds is None
            or latest_offset > self.latest_replay_offset_milliseconds
        )
        if offset_advanced:
            self.latest_replay_offset_milliseconds = latest_offset
        return token_changed or offset_advanced


# ---------------------------------------------------------------------------
# Stateless helpers (no downloader dependency) — kept at module scope so they
# stay independently unit-testable.
# ---------------------------------------------------------------------------


def _latest_replay_action_offset_milliseconds(
    actions: list[JSONDict],
) -> float | None:
    """Return the greatest valid replay-wrapper offset in an action page."""
    latest_offset: float | None = None
    for action in actions:
        replay_action = get_dict(action, "replayChatItemAction")
        raw_offset = get_str(replay_action, "videoOffsetTimeMsec")
        try:
            offset = float(raw_offset)
        except ValueError:
            continue
        if not math.isfinite(offset) or offset < 0:
            continue
        latest_offset = max(latest_offset or 0, offset)
    return latest_offset


def _profiled_innertube_context(
    ytcfg: JSONDict,
    profile_name: object,
) -> JSONDict:
    """Return an Innertube context adjusted for the active request profile."""
    context = _get_innertube_context(ytcfg)
    return apply_request_profile_to_innertube_context(context, profile_name)


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
        detail = str(error_message).casefold().rstrip(".")
        if detail in {
            "replay disabled",
            "chat replay is disabled",
            "live chat replay is disabled",
        }:
            raise NoChatReplay(str(error_message))
        msg = (
            f"YouTube rejected the chat continuation (HTTP 400): {error_message}. "
            "Try a fresh retrieval with --request_profile youtube_android or "
            "youtube_ios. This response does not establish replay availability."
        )
        raise ChatDownloaderError(msg)
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
            sample_limit=YOUTUBE_DEBUG_SAMPLE_LIMIT,
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


def _advance_continuation_loop(
    ctx: _ChatContext,
    yt_info: JSONDict,
) -> bool:
    """Advance continuation state and return True when iteration should stop."""
    cont_result = parse_continuation_response(yt_info)
    _log_continuation_debug_info(cont_result)
    ctx.loop_state = update_state_from_result(ctx.loop_state, cont_result)

    if cont_result.is_end:
        return True

    poll_delay_ms = _resolve_poll_delay_ms(
        cont_result.timeout_ms,
        replay_poll_interval=ctx.replay_poll_interval,
    )
    log("debug", f"Sleeping for {poll_delay_ms}ms.")
    polling_sleep(poll_delay_ms / _MS_PER_SECOND)

    if ctx.time_filter is not None:
        ctx.time_filter.end_page()
    return False


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


class _ContinuationLoop:
    """Own one run's setup, request/response handling, and iteration.

    Access the session via self.downloader, avoiding free-function self-threading
    or a wide structural protocol.
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
        self.paid_events = PaidEventCache()
        self._accepted_response = False
        self.ctx: _ChatContext
        self.progress = _ContinuationProgress(
            _YT_MAX_NO_PROGRESS_POLLS, _YT_MAX_PROFILE_FALLBACKS
        )
        self._capture = BoundedSampleCapture(
            _SUCCESSFUL_RESPONSE_CAPTURE_ENV,
            _SUCCESSFUL_RESPONSE_CAPTURE_LIMIT,
        )

    # -- setup --------------------------------------------------------------

    def _apply_session_headers(self, init_page: str) -> None:
        """Install the InnerTube auth and content-type headers on the session."""
        self.downloader.replace_session_headers(
            _generate_headers(
                self.ytcfg, self.downloader, _YT_HOME, _generate_sapisidhash_header
            ),
            _MANAGED_API_HEADER_NAMES,
        )
        self.downloader.update_session_headers(
            {"content-type": "application/json", "referer": init_page},
        )

    def _build_context(self) -> _ChatContext:
        """Validate message groups/types and update session headers for setup.

        Return context so setup no longer needs the downloader.

        Raises:
            NoContinuation: Requested chat type index is absent.
            InvalidParameter: Unknown message group.
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
            replay_poll_interval=(
                params.youtube_replay_poll_interval if is_replay else None
            ),
        )

    # -- response handling --------------------------------------------------

    def _handle_continuation_response(
        self, yt_info: JSONDict, continuation_params: JSONDict
    ) -> None:
        """Refresh response credentials, log request context, and surface API errors."""
        auth = _generate_sapisidhash_header(self.downloader, _YT_HOME, self.ytcfg)
        visitor_data = extract_visitor_data(yt_info)
        if visitor_data:
            self.downloader.update_session_headers({"x-goog-visitor-id": visitor_data})
            log("debug", "Updated visitor data")
        if auth:
            self.downloader.update_session_headers({"authorization": auth})

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
        _raise_if_api_error(yt_info)

    # -- profile fallback ---------------------------------------------------

    def _attempt_profile_fallback(
        self, reason: str = "repeated incomplete continuation responses"
    ) -> bool:
        """Try the next request profile on incomplete data; True means retry.

        If disabled or exhausted, return False; callers re-raise the original error.
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
            f"Switching YouTube request profile after {reason}: {next_profile}",
        )
        return True

    def _recover_incomplete_continuation(
        self, reason: str = "repeated incomplete continuation responses"
    ) -> bool:
        """Try incomplete-continuation profile fallback.

        Return True to retry, or False to re-raise the active
        IncompleteContinuationError.
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
        if not self._attempt_profile_fallback(reason):
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
        self.downloader.replace_session_headers(
            _generate_headers(
                self.ytcfg,
                self.downloader,
                _YT_HOME,
                _generate_sapisidhash_header,
            ),
            _MANAGED_API_HEADER_NAMES,
        )
        return True

    def _retry_rejected_initial_replay(self, response: JSONDict) -> bool:
        """Try the next profile for an initial replay INVALID_ARGUMENT error.

        Reuse continuation/seek bounds. Never restart or switch on this terminal
        error after an accepted response.
        """
        error = get_dict(response, "error")
        if (
            self._accepted_response
            or not self.ctx.is_replay
            or str(error.get("code")) != "400"
            or get_str(error, "status") != "INVALID_ARGUMENT"
        ):
            return False
        return self._recover_incomplete_continuation(
            "a rejected initial replay request"
        )

    # -- main loop ----------------------------------------------------------

    def run(
        self,
    ) -> Generator[JSONDict, None, None]:
        """Yield chat messages from a YouTube continuation endpoint."""
        self.ctx = self._build_context()
        ctx = self.ctx

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

            if self._retry_rejected_initial_replay(yt_info):
                continue
            self._handle_continuation_response(yt_info, continuation_params)
            self._accepted_response = True

            info = multi_get(yt_info, "continuationContents", "liveChatContinuation")
            if not info:
                summary = summarize_continuation_payload(yt_info)
                msg = (
                    "Missing continuationContents.liveChatContinuation in "
                    "response body. "
                    f"Summary: {summary}"
                )
                raise IncompleteContinuationError(msg)

            self._capture.capture("youtube-continuation-response", yt_info)

            actions = info.get("actions") or []
            stop_requested: bool = yield from _process_actions(
                actions,
                ctx.offset,
                ctx.msg_filter,
                ctx.time_filter,
                ctx.loop_state,
                ctx.live_start_time_ms,
                is_replay=ctx.is_replay,
                paid_events=self.paid_events,
            )
            if stop_requested:
                return

            if _advance_continuation_loop(ctx, yt_info):
                break

            made_progress = self.progress.response_advanced(
                actions,
                token_changed=(ctx.loop_state.continuation != token_before_request),
                is_replay=ctx.is_replay,
            )
            if self.progress.register_poll(made_progress=made_progress):
                msg = (
                    "No progress on YouTube continuation: "
                    f"{self.progress.max_no_progress_polls} consecutive polls "
                    "without continuation-token or replay-offset advancement. "
                    "The live chat may have ended without a terminator, or the "
                    "token is stale."
                )
                raise NoContinuation(msg)

        end_msg: JSONDict = {
            "message_type": "chat_ended",
            "action_type": "chat_ended",
            "message": None,
        }
        if ctx.msg_filter.should_add(end_msg):
            yield end_msg

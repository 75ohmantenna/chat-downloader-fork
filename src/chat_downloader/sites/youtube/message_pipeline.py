# SPDX-License-Identifier: MIT

"""Pure YouTube action processing: parse, validate/finalize, then type/time filters.

No network/logging side effects. process_pipeline_action returns a PipelineResult.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast

from chat_downloader.debugging import log

from .continuation_helpers import (
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
)
from .parsing.actions_handlers_validation import (
    is_known_ignored_message_type,
    validate_and_finalize_message,
)
from .parsing.actions_router import (
    ProcessedAction,
    is_known_ignored_action,
    process_action,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
    from chat_downloader.utils.json_types import JSONDict

    from .continuation_helpers import ContinuationLoopState
    from .paid_events import PaidEventCache

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class NonEmissionReason(StrEnum):
    """Bounded reasons why a processed action did not produce a message."""

    KNOWN_IGNORED_ACTION = "known ignored/control actions"
    KNOWN_IGNORED_MESSAGE = "known ignored renderers"
    UNPARSED_ACTION = "unparsed actions"
    INVALID_MESSAGE = "invalid messages"
    MESSAGE_FILTERED = "message type/group filtered"
    TIME_RANGE_FILTERED = "time-range filtered"
    TIME_RANGE_STOPPED = "time-range stop"


_SKIP_NON_EMISSION_REASONS = frozenset(
    {
        NonEmissionReason.KNOWN_IGNORED_ACTION,
        NonEmissionReason.KNOWN_IGNORED_MESSAGE,
        NonEmissionReason.UNPARSED_ACTION,
        NonEmissionReason.INVALID_MESSAGE,
        NonEmissionReason.MESSAGE_FILTERED,
        NonEmissionReason.TIME_RANGE_FILTERED,
    }
)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Typed outcome of processing one raw action.

    Attributes:
        disposition: "yield" emits message; "skip" advances past a filtered action;
            "stop" ends iteration when the time-range filter signals end of stream.
        message: Fully parsed dict for "yield", otherwise None.
        non_emission_reason: Bounded diagnostic reason for a processed non-yield.
    """

    disposition: Literal["yield", "skip", "stop"]
    message: dict[str, Any] | None = None
    non_emission_reason: NonEmissionReason | None = None

    def __post_init__(self) -> None:
        """Enforce the message/reason contract for each disposition."""
        disposition = cast("str", self.disposition)
        if disposition == "yield":
            if self.message is None or self.non_emission_reason is not None:
                msg = "A yielding pipeline result requires a message and no reason"
                raise ValueError(msg)
            return
        if disposition == "skip":
            if (
                self.message is not None
                or not isinstance(self.non_emission_reason, NonEmissionReason)
                or self.non_emission_reason not in _SKIP_NON_EMISSION_REASONS
            ):
                msg = "A skipped pipeline result requires a skip reason and no message"
                raise ValueError(msg)
            return
        if disposition == "stop":
            if (
                self.message is not None
                or self.non_emission_reason is not NonEmissionReason.TIME_RANGE_STOPPED
            ):
                msg = "A stopped pipeline result requires only the stop reason"
                raise ValueError(msg)
            return
        msg = f"Unknown pipeline disposition: {disposition}"
        raise ValueError(msg)


def _validate_pipeline_message(
    result: ProcessedAction | None,
) -> dict[str, Any] | None:
    """Validate a parsed action result and return the finalized message."""
    if result is None:
        return None

    return validate_and_finalize_message(
        result.parsed_data,
        result.original_item,
        result.message_type,
        result.action_type,
    )


def _check_time_filter(
    validated_data: dict[str, Any],
    time_filter: TimeRangeFilter | None,
) -> Literal["yield", "skip", "stop"]:
    """Evaluate the optional time filter for a validated message."""
    if time_filter is None:
        return "yield"

    time_result = time_filter.check(validated_data)
    if time_result == "skip":
        return "skip"
    if time_result == "stop":
        return "stop"
    return "yield"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process_pipeline_action(
    action: dict[str, Any],
    offset: float,
    msg_filter: MessageFilter,
    time_filter: TimeRangeFilter | None,
    paid_events: PaidEventCache | None = None,
) -> PipelineResult:
    """Parse, validate/finalize, then type/group- and time-filter a raw API action.

    process_action ignores unknown/ignored actions;
    validate_and_finalize_message checks required fields and post-processes,
    rejecting malformed messages. MessageFilter.should_add excludes false results;
    TimeRangeFilter.check skips on "skip" and ends the stream on "stop".

    Args:
        action: Raw YouTube API action to parse and filter.
        offset: Replay offset in seconds, passed to process_action.
        msg_filter: Message type/group inclusion filter.
        time_filter: Optional range filter; None for live streams.
        paid_events: Optional per-run cache enriching sparse paid tickers.
    """
    known_ignored_action = is_known_ignored_action(action)
    parsed_action = process_action(action, offset)
    if parsed_action is None:
        reason = (
            NonEmissionReason.KNOWN_IGNORED_ACTION
            if known_ignored_action
            else NonEmissionReason.UNPARSED_ACTION
        )
        return PipelineResult(disposition="skip", non_emission_reason=reason)

    validated_data = _validate_pipeline_message(parsed_action)
    if validated_data is None:
        reason = (
            NonEmissionReason.KNOWN_IGNORED_MESSAGE
            if is_known_ignored_message_type(parsed_action.message_type)
            else NonEmissionReason.INVALID_MESSAGE
        )
        return PipelineResult(disposition="skip", non_emission_reason=reason)

    if paid_events is not None:
        paid_events.enrich(validated_data)

    if not msg_filter.should_add(validated_data):
        return PipelineResult(
            disposition="skip",
            non_emission_reason=NonEmissionReason.MESSAGE_FILTERED,
        )

    time_result = _check_time_filter(validated_data, time_filter)
    if time_result == "skip":
        return PipelineResult(
            disposition="skip",
            non_emission_reason=NonEmissionReason.TIME_RANGE_FILTERED,
        )
    if time_result == "stop":
        return PipelineResult(
            disposition="stop",
            non_emission_reason=NonEmissionReason.TIME_RANGE_STOPPED,
        )

    return PipelineResult(disposition="yield", message=validated_data)


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


def _process_actions(
    actions: list[JSONDict],
    offset: float | None,
    msg_filter: MessageFilter,
    time_filter: TimeRangeFilter | None,
    loop_state: ContinuationLoopState,
    live_start_time_ms: int,
    *,
    is_replay: bool,
    paid_events: PaidEventCache | None = None,
) -> Generator[JSONDict, None, bool]:
    """Filter raw ``liveChatContinuation`` actions and yield accepted messages.

    Updates nonnegative ``loop_state.offset_milliseconds`` from usable live
    timestamps (signed presentation timing preserves backlog ordering).

    Args:
        actions: Raw actions from a ``liveChatContinuation`` response.
        offset: Clip/replay offset in seconds, passed to the pipeline.
        msg_filter: Message type/group inclusion filter.
        time_filter: Optional replay time-range filter.
        loop_state: Mutable continuation state updated with live offsets.
        live_start_time_ms: Epoch-ms baseline for live offsets.
        is_replay: Suppress live-timing enrichment for replay streams.
        paid_events: Per-run cache enriching sparse paid tickers.

    Returns:
        True on a "stop" disposition (terminate the outer loop).
    """
    processed_action_count = 0
    emitted_message_count = 0
    non_emission_counts: Counter[NonEmissionReason] = Counter()
    for action in actions:
        pipeline_result = process_pipeline_action(
            action,
            offset or 0.0,
            msg_filter,
            time_filter,
            paid_events,
        )
        processed_action_count += 1
        if pipeline_result.non_emission_reason is not None:
            non_emission_counts[pipeline_result.non_emission_reason] += 1
        if pipeline_result.disposition == "skip":
            continue
        if pipeline_result.disposition == "stop":
            _log_poll_action_diagnostics(
                processed_action_count,
                emitted_message_count,
                non_emission_counts,
            )
            return True
        if not is_replay and pipeline_result.message is not None:
            _apply_live_timing(pipeline_result.message, loop_state, live_start_time_ms)
        if pipeline_result.message is not None:
            emitted_message_count += 1
            yield pipeline_result.message

    _log_poll_action_diagnostics(
        processed_action_count,
        emitted_message_count,
        non_emission_counts,
    )
    return False


def _log_poll_action_diagnostics(
    processed_count: int,
    emitted_count: int,
    non_emission_counts: Counter[NonEmissionReason],
) -> None:
    """Log bounded aggregate action outcomes for one continuation poll."""
    non_emitted_count = sum(non_emission_counts.values())
    message = (
        f"Processed actions in poll: {processed_count}; "
        f"emitted messages: {emitted_count}; "
        f"non-emitted actions: {non_emitted_count}"
    )
    reason_counts = ", ".join(
        f"{reason.value}: {non_emission_counts[reason]}"
        for reason in NonEmissionReason
        if non_emission_counts[reason]
    )
    if reason_counts:
        message += f" ({reason_counts})"
    log("debug", message)

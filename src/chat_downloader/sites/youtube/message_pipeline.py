# SPDX-License-Identifier: MIT

"""Pure YouTube action processing: parse, validate/finalize, then type/time filters.

No network/logging side effects. process_pipeline_action returns a PipelineResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast

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
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter

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

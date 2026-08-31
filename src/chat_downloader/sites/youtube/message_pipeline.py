# SPDX-License-Identifier: MIT

"""YouTube chat message processing pipeline.

Pure functions that take a raw action dict and run it through the full
message pipeline: parse → validate/finalize → message-type filter →
time-range filter.  No network calls or logging side effects.

Public surface
--------------
- :class:`PipelineResult` — typed outcome of processing one action.
- :func:`process_pipeline_action` — main entry point.
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
    """Outcome of processing a single raw action through the message pipeline.

    :param disposition: What the caller should do with this action.

        * ``"yield"`` — ``message`` is ready to be emitted to the consumer.
        * ``"skip"`` — action was filtered out; move to the next one.
        * ``"stop"`` — time-range filter signaled end of stream; the caller
          should stop iterating.

    :param message: Fully-parsed message dict when ``disposition == "yield"``,
        otherwise ``None``.
    :param non_emission_reason: Bounded diagnostic reason when the action was
        processed but did not yield a message.
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
) -> PipelineResult:
    """Run a single raw action through the full message pipeline.

    Steps:

    1. :func:`~.parsing.actions_router.process_action` — parse the raw action
       dict into a typed message dict; returns ``None`` for ignored or unknown
       actions.
    2. :func:`~.parsing.actions_handlers_validation.validate_and_finalize_message`
       — validate required fields and apply post-processing; returns ``None``
       when the message is malformed.
    3. :meth:`chat_downloader.sites.filters.MessageFilter.should_add` —
       message-type / group filter; ``False`` means the message is excluded.
    4. :meth:`chat_downloader.sites.filters.TimeRangeFilter.check` (when
       *time_filter* is not ``None``) — time-range filter; ``"skip"`` skips
       the message, ``"stop"`` signals end of stream.

    :param action: Raw action dict from the YouTube live-chat API.
    :param offset: Time offset in seconds for replay chat (passed to
        :func:`~.parsing.actions_router.process_action`).
    :param msg_filter: Message-type / group filter instance.
    :param time_filter: Optional time-range filter; pass ``None`` for
        live (non-replay) streams.
    :return: :class:`PipelineResult` with ``disposition`` and optional
        ``message``.
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

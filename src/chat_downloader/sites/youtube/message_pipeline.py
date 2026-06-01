# SPDX-License-Identifier: MIT

"""YouTube chat message processing pipeline.

Pure functions that take a raw action dict and run it through the full
message pipeline: parse → validate/finalise → message-type filter →
time-range filter.  No network calls or logging side effects.

Public surface
--------------
- :class:`PipelineResult` — typed outcome of processing one action.
- :func:`process_pipeline_action` — main entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .parsing.actions_handlers import validate_and_finalize_message
from .parsing.actions_router import process_action

if TYPE_CHECKING:
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of processing a single raw action through the message pipeline.

    :param disposition: What the caller should do with this action.

        * ``"yield"`` — ``message`` is ready to be emitted to the consumer.
        * ``"skip"`` — action was filtered out; move to the next one.
        * ``"stop"`` — time-range filter signalled end of stream; the caller
          should stop iterating.

    :param message: Fully-parsed message dict when ``disposition == "yield"``,
        otherwise ``None``.
    """

    disposition: Literal["yield", "skip", "stop"]
    message: dict[str, Any] | None = None


def _validate_pipeline_message(
    result: tuple[dict[str, Any], Any, str | None, str] | None,
) -> dict[str, Any] | None:
    """Validate a parsed action result and return the finalized message."""
    if result is None:
        return None

    data, original_item, original_message_type, original_action_type = result
    return validate_and_finalize_message(
        data,
        original_item,
        original_message_type,
        original_action_type,
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

    1. :func:`~.parsing.actions.process_action` — parse the raw action dict
       into a typed message dict; returns ``None`` for ignored/unknown actions.
    2. :func:`~.parsing.actions.validate_and_finalize_message` — validate
       required fields and apply any post-processing; returns ``None`` when
       the message is malformed.
    3. :meth:`~..common.MessageFilter.should_add` — message-type / group
       filter; ``False`` means the message is excluded.
    4. :meth:`~..common.TimeRangeFilter.check` (when *time_filter* is not
       ``None``) — time-range filter; ``"skip"`` skips the message,
       ``"stop"`` signals end of stream.

    :param action: Raw action dict from the YouTube live-chat API.
    :param offset: Time offset in seconds for replay chat (passed to
        :func:`~.parsing.actions.process_action`).
    :param msg_filter: Message-type / group filter instance.
    :param time_filter: Optional time-range filter; pass ``None`` for
        live (non-replay) streams.
    :return: :class:`PipelineResult` with ``disposition`` and optional
        ``message``.
    """
    validated_data = _validate_pipeline_message(process_action(action, offset))
    if validated_data is None:
        return PipelineResult(disposition="skip")

    if not msg_filter.should_add(validated_data):
        return PipelineResult(disposition="skip")

    time_result = _check_time_filter(validated_data, time_filter)
    if time_result != "yield":
        return PipelineResult(disposition=time_result)

    return PipelineResult(disposition="yield", message=validated_data)

# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from unittest.mock import Mock

import pytest

from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuation_helpers import (
    ContinuationLoopState,
    enrich_live_message_timing,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
    _process_actions,
)
from tests.youtube_third_helpers import item_action, patch


def _actions(actions, **overrides):
    options = {
        "offset": None,
        "msg_filter": MessageFilter({}),
        "time_filter": None,
        "loop_state": ContinuationLoopState(continuation="tok"),
        "live_start_time_ms": 0,
        "is_replay": True,
    }
    return _process_actions(actions, **(options | overrides))


def _yield(text):
    return PipelineResult(disposition="yield", message={"text": text})


def _skip(reason):
    return PipelineResult(disposition="skip", non_emission_reason=reason)


def _assert_poll_summary(log_calls, processed, emitted, non_emitted, reasons):
    # Counts, reasons, severity and cardinality are the diagnostic contract.
    assert len(log_calls) == 1
    level, summary = log_calls[0]
    assert level == "debug"
    totals, _, breakdown = summary.partition(" (")
    assert [int(value) for value in re.findall(r": (\d+)", totals)] == [
        processed,
        emitted,
        non_emitted,
    ]
    assert {
        reason.strip(): int(count)
        for reason, count in re.findall(r"([^,:]+): (\d+)", breakdown.rstrip(")"))
    } == reasons


def test_replay_unknown_actions_count_as_record_loss(monkeypatch):
    reasons = [
        NonEmissionReason.KNOWN_IGNORED_ACTION,
        NonEmissionReason.UNPARSED_ACTION,
        NonEmissionReason.INVALID_MESSAGE,
    ]
    process = Mock(side_effect=[_skip(reason) for reason in reasons])
    patch(monkeypatch, "message_pipeline.process_pipeline_action", process)
    diagnostics: dict[str, object] = {}
    assert list(_actions([{} for _ in reasons], diagnostics=diagnostics)) == []
    assert diagnostics["parse_error"] == 2


@pytest.mark.parametrize(
    ("results", "expected", "stopped", "diagnostics"),
    [
        (
            [_yield("hello"), _yield("world")],
            ["hello", "world"],
            False,
            (2, 2, 0, {}),
        ),
        (
            [
                _yield("first"),
                PipelineResult(
                    disposition="stop",
                    non_emission_reason=NonEmissionReason.TIME_RANGE_STOPPED,
                ),
                _yield("should-not-appear"),
            ],
            ["first"],
            True,
            (2, 1, 1, {"time-range stop": 1}),
        ),
        (
            [_skip(NonEmissionReason.UNPARSED_ACTION), _yield("kept")],
            ["kept"],
            False,
            (2, 1, 1, {"unparsed actions": 1}),
        ),
        (
            [
                _skip(NonEmissionReason.MESSAGE_FILTERED),
                _skip(NonEmissionReason.KNOWN_IGNORED_ACTION),
                _yield("kept"),
            ],
            ["kept"],
            False,
            (
                3,
                1,
                2,
                {"known ignored/control actions": 1, "message type/group filtered": 1},
            ),
        ),
        ([], [], False, (0, 0, 0, {})),
    ],
)
def test_process_dispositions(monkeypatch, results, expected, stopped, diagnostics):
    log_calls = []
    patch(monkeypatch, "message_pipeline.log", lambda *args: log_calls.append(args))
    process = Mock(side_effect=results)
    patch(monkeypatch, "message_pipeline.process_pipeline_action", process)
    gen = _actions([{"id": index} for index in range(len(results))])
    messages = []
    with pytest.raises(StopIteration) as exc:
        while True:
            messages.append(next(gen))
    assert messages == [{"text": text} for text in expected]
    assert exc.value.value is stopped
    _assert_poll_summary(log_calls, *diagnostics)


def test_process_composes_parser_filters_and_poll_diagnostics(monkeypatch):
    actions = [
        {"addInteractivityWidgetAction": {}},
        {},
        {
            "removeChatItemAction": {
                "targetItemId": "deleted-message",
                "timestampUsec": "2",
            }
        },
        item_action("liveChatTextMessageRenderer", {"timestampUsec": "1"}),
    ]
    log_calls: list[tuple] = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.log",
        lambda *args: log_calls.append(args),
    )
    result = list(
        _actions(
            actions,
            msg_filter=MessageFilter(_MESSAGE_GROUPS, types_to_add=["text_message"]),
        )
    )
    assert [message["message_type"] for message in result] == ["text_message"]
    _assert_poll_summary(
        log_calls,
        4,
        1,
        3,
        {
            "known ignored/control actions": 1,
            "unparsed actions": 1,
            "message type/group filtered": 1,
        },
    )


@pytest.mark.parametrize(
    ("initial_offset", "expected_offset"), [(None, 0), (5000, 5000)]
)
def test_signed_backlog_and_monotonic_polling(
    monkeypatch, initial_offset, expected_offset
):
    state = ContinuationLoopState(
        continuation="tok", offset_milliseconds=initial_offset
    )
    patch(
        monkeypatch,
        "message_pipeline.process_pipeline_action",
        Mock(
            return_value=PipelineResult(
                disposition="yield", message={"timestamp": 500_000}
            )
        ),
    )
    messages = list(
        _actions(
            [{"id": 1}], loop_state=state, live_start_time_ms=1000, is_replay=False
        )
    )
    assert messages == [
        {"timestamp": 500_000, "time_in_seconds": -0.5, "time_text": "-0:00"}
    ]
    assert state.offset_milliseconds == expected_offset


@pytest.mark.parametrize(
    "message", [{"time_in_seconds": 5.0, "body": "hello"}, {"time_text": "0:05"}]
)
def test_existing_timing_preserved(message):
    original = message.copy()
    enrich_live_message_timing(message, live_offset_milliseconds=1000)
    assert message == original

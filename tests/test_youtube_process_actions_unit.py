# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import Mock

import pytest

from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuation import (
    ContinuationLoopState,
    _process_actions,
    enrich_live_message_timing,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
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


@pytest.mark.parametrize(
    ("results", "expected", "stopped"),
    [
        ([_yield("hello"), _yield("world")], ["hello", "world"], False),
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
        ),
        ([_skip(NonEmissionReason.UNPARSED_ACTION), _yield("kept")], ["kept"], False),
        (
            [
                _skip(NonEmissionReason.MESSAGE_FILTERED),
                _skip(NonEmissionReason.KNOWN_IGNORED_ACTION),
                _yield("kept"),
            ],
            ["kept"],
            False,
        ),
        ([], [], False),
    ],
)
def test_process_dispositions(monkeypatch, results, expected, stopped):
    process = Mock(side_effect=results)
    patch(monkeypatch, "continuation.process_pipeline_action", process)
    gen = _actions([{"id": index} for index in range(len(results))])
    messages = []
    with pytest.raises(StopIteration) as exc:
        while True:
            messages.append(next(gen))
    assert messages == [{"text": text} for text in expected]
    assert exc.value.value is stopped
    assert process.call_count == (2 if stopped else len(results))


def test_process_composes_parser_and_filters():
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
    result = list(
        _actions(
            actions,
            msg_filter=MessageFilter(_MESSAGE_GROUPS, types_to_add=["text_message"]),
        )
    )
    assert [message["message_type"] for message in result] == ["text_message"]


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
        "continuation.process_pipeline_action",
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

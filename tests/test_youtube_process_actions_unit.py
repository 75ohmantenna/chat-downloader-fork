# SPDX-License-Identifier: MIT

from __future__ import annotations

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


def _actions(actions, **overrides):
    options = {
        "offset": None,
        "msg_filter": MessageFilter({}),
        "time_filter": None,
        "loop_state": ContinuationLoopState(
            continuation="tok",
            offset_milliseconds=None,
        ),
        "live_start_time_ms": 0,
        "is_replay": True,
    }
    return _process_actions(actions, **(options | overrides))


def _yield(text):
    return PipelineResult(disposition="yield", message={"text": text})


def _skip(reason):
    return PipelineResult(disposition="skip", non_emission_reason=reason)


@pytest.mark.parametrize(
    ("results", "expected", "stopped", "summary"),
    [
        (
            [_yield("hello"), _yield("world")],
            [{"text": "hello"}, {"text": "world"}],
            False,
            None,
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
            [{"text": "first"}],
            True,
            (
                "Processed actions in poll: 2; emitted messages: 1; "
                "non-emitted actions: 1 (time-range stop: 1)"
            ),
        ),
        (
            [_skip(NonEmissionReason.UNPARSED_ACTION), _yield("kept")],
            [{"text": "kept"}],
            False,
            None,
        ),
        (
            [
                _skip(NonEmissionReason.MESSAGE_FILTERED),
                _skip(NonEmissionReason.KNOWN_IGNORED_ACTION),
                _yield("kept"),
            ],
            [{"text": "kept"}],
            False,
            (
                "Processed actions in poll: 3; emitted messages: 1; "
                "non-emitted actions: 2 (known ignored/control actions: 1, "
                "message type/group filtered: 1)"
            ),
        ),
        (
            [],
            [],
            False,
            "Processed actions in poll: 0; emitted messages: 0; non-emitted actions: 0",
        ),
    ],
    ids=["yield", "stop", "skip", "bounded-counts", "empty-poll"],
)
def test_process_actions_dispositions(monkeypatch, results, expected, stopped, summary):
    pending = iter(results)
    logs = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.process_pipeline_action",
        lambda *_args: next(pending),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.log",
        lambda *args: logs.append(args),
    )
    gen = _actions([{"id": index} for index in range(len(results))])
    messages = []
    with pytest.raises(StopIteration) as exc:
        while True:
            messages.append(next(gen))

    assert messages == expected
    assert exc.value.value is stopped
    if summary is not None:
        assert logs == [("debug", summary)]


def test_process_actions_composes_parser_filters_and_poll_diagnostics(monkeypatch):
    actions = [
        {"addInteractivityWidgetAction": {}},
        {},
        {
            "removeChatItemAction": {
                "targetItemId": "deleted-message",
                "timestampUsec": "2",
            },
        },
        {
            "addChatItemAction": {
                "item": {"liveChatTextMessageRenderer": {"timestampUsec": "1"}},
            },
        },
    ]
    logs = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.log",
        lambda *args: logs.append(args),
    )
    result = list(
        _actions(
            actions,
            msg_filter=MessageFilter(_MESSAGE_GROUPS, types_to_add=["text_message"]),
        )
    )
    assert [message["message_type"] for message in result] == ["text_message"]
    assert logs == [
        (
            "debug",
            (
                "Processed actions in poll: 4; emitted messages: 1; "
                "non-emitted actions: 3 (known ignored/control actions: 1, "
                "unparsed actions: 1, message type/group filtered: 1)"
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("initial_offset", "expected_offset"), [(None, 0), (5000, 5000)]
)
def test_process_actions_keeps_signed_backlog_and_monotonic_polling(
    monkeypatch,
    initial_offset,
    expected_offset,
):
    state = ContinuationLoopState(
        continuation="tok", offset_milliseconds=initial_offset
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.process_pipeline_action",
        lambda *_args, **_kwargs: PipelineResult(
            disposition="yield",
            message={"timestamp": 500_000},
        ),
    )
    messages = list(
        _actions(
            [{"id": 1}],
            loop_state=state,
            live_start_time_ms=1000,
            is_replay=False,
        )
    )
    assert messages == [
        {"timestamp": 500_000, "time_in_seconds": -0.5, "time_text": "-0:00"},
    ]
    assert state.offset_milliseconds == expected_offset


@pytest.mark.parametrize(
    "message",
    [
        {"time_in_seconds": 5.0, "body": "hello"},
        {"time_text": "0:05"},
    ],
)
def test_enrich_live_message_timing_preserves_existing_timing(message):
    original = message.copy()
    enrich_live_message_timing(message, live_offset_milliseconds=1000)
    assert message == original

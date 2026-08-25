# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

from chat_downloader.sites.youtube.continuation import (
    ContinuationLoopState,
    _process_actions,
)
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
)


def _make_loop_state() -> ContinuationLoopState:
    return ContinuationLoopState(continuation="tok", offset_milliseconds=None)


def test_process_actions_yields_accepted_messages() -> None:
    """_process_actions yields messages whose disposition is 'yield'."""
    from chat_downloader.sites.filters import MessageFilter

    msg_filter = MessageFilter({})
    actions = [{"id": 1}, {"id": 2}]
    pipeline_results = iter(
        [
            PipelineResult(disposition="yield", message={"text": "hello"}),
            PipelineResult(disposition="yield", message={"text": "world"}),
        ]
    )

    def fake_pipeline(action, offset, mf, tf):
        return next(pipeline_results)

    import chat_downloader.sites.youtube.continuation as mod

    original = mod.process_pipeline_action
    mod.process_pipeline_action = fake_pipeline
    try:
        result = list(
            _process_actions(
                actions,
                offset=None,
                msg_filter=msg_filter,
                time_filter=None,
                loop_state=_make_loop_state(),
                live_start_time_ms=0,
                is_replay=True,
            )
        )
    finally:
        mod.process_pipeline_action = original

    assert result == [{"text": "hello"}, {"text": "world"}]


def test_process_actions_stop_disposition_logs_reason_and_yields_nothing_after(
    monkeypatch,
) -> None:
    """A 'stop' action makes _process_actions stop and return True."""
    from chat_downloader.sites.filters import MessageFilter

    msg_filter = MessageFilter({})
    actions = [{"id": 1}, {"id": 2}, {"id": 3}]
    log_calls = []
    # First action: yield; second: stop; third should never be reached.
    pipeline_results = iter(
        [
            PipelineResult(disposition="yield", message={"text": "first"}),
            PipelineResult(
                disposition="stop",
                non_emission_reason=NonEmissionReason.TIME_RANGE_STOPPED,
            ),
            PipelineResult(
                disposition="yield",
                message={"text": "should-not-appear"},
            ),
        ]
    )

    def fake_pipeline(action, offset, mf, tf):
        return next(pipeline_results)

    import chat_downloader.sites.youtube.continuation as mod

    monkeypatch.setattr(mod, "log", lambda *args: log_calls.append(args))
    original = mod.process_pipeline_action
    mod.process_pipeline_action = fake_pipeline
    try:
        gen = _process_actions(
            actions,
            offset=None,
            msg_filter=msg_filter,
            time_filter=None,
            loop_state=_make_loop_state(),
            live_start_time_ms=0,
            is_replay=True,
        )
        messages = []
        stop_requested = False
        try:
            while True:
                messages.append(next(gen))
        except StopIteration as exc:
            stop_requested = exc.value
    finally:
        mod.process_pipeline_action = original

    assert messages == [{"text": "first"}]
    assert stop_requested is True
    assert log_calls == [
        (
            "debug",
            (
                "Processed actions in poll: 2; emitted messages: 1; "
                "non-emitted actions: 1 (time-range stop: 1)"
            ),
        )
    ]


def test_process_actions_skip_disposition_does_not_yield() -> None:
    """Messages with 'skip' disposition are silently dropped."""
    from chat_downloader.sites.filters import MessageFilter

    msg_filter = MessageFilter({})
    actions = [{"id": 1}, {"id": 2}]
    pipeline_results = iter(
        [
            PipelineResult(
                disposition="skip",
                non_emission_reason=NonEmissionReason.UNPARSED_ACTION,
            ),
            PipelineResult(disposition="yield", message={"text": "kept"}),
        ]
    )

    def fake_pipeline(action, offset, mf, tf):
        return next(pipeline_results)

    import chat_downloader.sites.youtube.continuation as mod

    original = mod.process_pipeline_action
    mod.process_pipeline_action = fake_pipeline
    try:
        result = list(
            _process_actions(
                actions,
                offset=None,
                msg_filter=msg_filter,
                time_filter=None,
                loop_state=_make_loop_state(),
                live_start_time_ms=0,
                is_replay=True,
            )
        )
    finally:
        mod.process_pipeline_action = original

    assert result == [{"text": "kept"}]


def test_process_actions_logs_bounded_non_emission_counts(monkeypatch) -> None:
    from chat_downloader.sites.filters import MessageFilter

    msg_filter = MessageFilter({})
    log_calls = []
    pipeline_results = iter(
        [
            PipelineResult(
                disposition="skip",
                non_emission_reason=NonEmissionReason.MESSAGE_FILTERED,
            ),
            PipelineResult(
                disposition="skip",
                non_emission_reason=NonEmissionReason.KNOWN_IGNORED_ACTION,
            ),
            PipelineResult(disposition="yield", message={"text": "kept"}),
        ]
    )

    def fake_pipeline(action, offset, mf, tf):
        return next(pipeline_results)

    import chat_downloader.sites.youtube.continuation as mod

    monkeypatch.setattr(mod, "log", lambda *args: log_calls.append(args))
    original = mod.process_pipeline_action
    mod.process_pipeline_action = fake_pipeline
    try:
        gen = _process_actions(
            [{"id": 1}, {"id": 2}, {"id": 3}],
            offset=None,
            msg_filter=msg_filter,
            time_filter=None,
            loop_state=_make_loop_state(),
            live_start_time_ms=0,
            is_replay=True,
        )
        messages = []
        stop_requested = False
        try:
            while True:
                messages.append(next(gen))
        except StopIteration as exc:
            stop_requested = exc.value
    finally:
        mod.process_pipeline_action = original

    assert messages == [{"text": "kept"}]
    assert stop_requested is False
    assert log_calls == [
        (
            "debug",
            (
                "Processed actions in poll: 3; emitted messages: 1; "
                "non-emitted actions: 2 (known ignored/control actions: 1, "
                "message type/group filtered: 1)"
            ),
        )
    ]


def test_process_actions_logs_one_zero_summary_for_empty_poll(monkeypatch) -> None:
    from chat_downloader.sites.filters import MessageFilter

    log_calls = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.log",
        lambda *args: log_calls.append(args),
    )

    assert (
        list(
            _process_actions(
                [],
                offset=None,
                msg_filter=MessageFilter({}),
                time_filter=None,
                loop_state=_make_loop_state(),
                live_start_time_ms=0,
                is_replay=True,
            )
        )
        == []
    )
    assert log_calls == [
        (
            "debug",
            (
                "Processed actions in poll: 0; emitted messages: 0; "
                "non-emitted actions: 0"
            ),
        )
    ]


def test_process_actions_composes_parser_filters_and_poll_diagnostics(
    monkeypatch,
) -> None:
    """Drive real parser/filter collaborators through the poll aggregation."""
    from chat_downloader.sites.filters import MessageFilter
    from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS

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
                "item": {
                    "liveChatTextMessageRenderer": {
                        "timestampUsec": "1",
                    },
                },
            },
        },
    ]
    log_calls = []

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.log",
        lambda *args: log_calls.append(args),
    )

    result = list(
        _process_actions(
            actions,
            offset=None,
            msg_filter=MessageFilter(
                _MESSAGE_GROUPS,
                types_to_add=["text_message"],
            ),
            time_filter=None,
            loop_state=_make_loop_state(),
            live_start_time_ms=0,
            is_replay=True,
        )
    )

    assert [message["message_type"] for message in result] == ["text_message"]
    assert log_calls == [
        (
            "debug",
            (
                "Processed actions in poll: 4; emitted messages: 1; "
                "non-emitted actions: 3 (known ignored/control actions: 1, "
                "unparsed actions: 1, message type/group filtered: 1)"
            ),
        )
    ]


def test_process_actions_keeps_backlog_timing_signed_and_polling_nonnegative(
    monkeypatch,
) -> None:
    from chat_downloader.sites.filters import MessageFilter

    state = _make_loop_state()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.process_pipeline_action",
        lambda *_args, **_kwargs: SimpleNamespace(
            disposition="yield",
            message={"timestamp": 500_000},
            non_emission_reason=None,
        ),
    )

    messages = list(
        _process_actions(
            [{"id": 1}],
            offset=None,
            msg_filter=MessageFilter({}),
            time_filter=None,
            loop_state=state,
            live_start_time_ms=1000,
            is_replay=False,
        )
    )

    assert messages == [
        {
            "timestamp": 500_000,
            "time_in_seconds": -0.5,
            "time_text": "-0:00",
        }
    ]
    assert state.offset_milliseconds == 0


def test_process_actions_does_not_regress_polling_offset_for_late_backlog(
    monkeypatch,
) -> None:
    from chat_downloader.sites.filters import MessageFilter

    state = ContinuationLoopState(continuation="tok", offset_milliseconds=5000)
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.process_pipeline_action",
        lambda *_args, **_kwargs: SimpleNamespace(
            disposition="yield",
            message={"timestamp": 500_000},
            non_emission_reason=None,
        ),
    )

    list(
        _process_actions(
            [{"id": 1}],
            offset=None,
            msg_filter=MessageFilter({}),
            time_filter=None,
            loop_state=state,
            live_start_time_ms=1000,
            is_replay=False,
        )
    )

    assert state.offset_milliseconds == 5000


def test_enrich_live_message_timing_skips_when_time_in_seconds_present() -> None:
    from chat_downloader.sites.youtube.continuation import (
        enrich_live_message_timing,
    )

    message = {"time_in_seconds": 5.0, "body": "hello"}
    enrich_live_message_timing(message, live_offset_milliseconds=1000)
    assert message["time_in_seconds"] == 5.0  # Unchanged — early return


def test_enrich_live_message_timing_skips_when_time_text_present() -> None:
    from chat_downloader.sites.youtube.continuation import (
        enrich_live_message_timing,
    )

    message = {"time_text": "0:05"}
    enrich_live_message_timing(message, live_offset_milliseconds=1000)
    assert "time_in_seconds" not in message  # Not added — early return

# SPDX-License-Identifier: MIT

from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, Any, cast

import pytest

from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    PipelineResult,
    _check_time_filter,
    _validate_pipeline_message,
    process_pipeline_action,
)
from chat_downloader.sites.youtube.parsing.actions_router import ProcessedAction

if TYPE_CHECKING:
    from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter


class _DummyMessageFilter:
    def __init__(self, should_add_result: bool = True) -> None:
        self.should_add_result = should_add_result
        self.seen_messages: list[dict[str, Any]] = []

    def should_add(self, message: dict[str, Any]) -> bool:
        self.seen_messages.append(message)
        return self.should_add_result


class _DummyTimeFilter:
    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.seen_messages: list[dict[str, Any]] = []

    def check(self, message: dict[str, Any]) -> str | None:
        self.seen_messages.append(message)
        return self.result


_PIPELINE_DISPOSITIONS = ("yield", "skip", "stop", "unknown")
_PIPELINE_MESSAGES = (None, {"message": "hello"})
_PIPELINE_REASONS = (
    None,
    *NonEmissionReason,
    *(reason.value for reason in NonEmissionReason),
    "unknown",
)
_VALID_PIPELINE_STATES = {
    ("yield", True, None),
    ("skip", False, NonEmissionReason.KNOWN_IGNORED_ACTION),
    ("skip", False, NonEmissionReason.KNOWN_IGNORED_MESSAGE),
    ("skip", False, NonEmissionReason.UNPARSED_ACTION),
    ("skip", False, NonEmissionReason.INVALID_MESSAGE),
    ("skip", False, NonEmissionReason.MESSAGE_FILTERED),
    ("skip", False, NonEmissionReason.TIME_RANGE_FILTERED),
    ("stop", False, NonEmissionReason.TIME_RANGE_STOPPED),
}


@pytest.mark.parametrize(
    ("disposition", "message", "reason"),
    tuple(product(_PIPELINE_DISPOSITIONS, _PIPELINE_MESSAGES, _PIPELINE_REASONS)),
)
def test_pipeline_result_accepts_only_valid_state_combinations(
    disposition,
    message,
    reason,
) -> None:
    state = (disposition, message is not None, reason)
    reason_has_valid_runtime_type = reason is None or isinstance(
        reason, NonEmissionReason
    )
    if reason_has_valid_runtime_type and state in _VALID_PIPELINE_STATES:
        result = PipelineResult(
            disposition=cast("Any", disposition),
            message=message,
            non_emission_reason=reason,
        )
        assert result.disposition == disposition
        assert result.message is message
        assert result.non_emission_reason is reason
        return

    with pytest.raises(ValueError):
        PipelineResult(
            disposition=cast("Any", disposition),
            message=message,
            non_emission_reason=reason,
        )


@pytest.mark.parametrize("reason", [reason.value for reason in NonEmissionReason])
def test_pipeline_result_rejects_raw_string_non_emission_reasons(reason) -> None:
    with pytest.raises(ValueError):
        PipelineResult(
            disposition="skip",
            non_emission_reason=cast("Any", reason),
        )


def test_validate_pipeline_message_returns_none_for_missing_parse_result() -> None:
    assert _validate_pipeline_message(None) is None


def test_validate_pipeline_message_returns_finalized_message(
    monkeypatch,
) -> None:
    parse_result = ProcessedAction(
        parsed_data={"message": "hello"},
        original_item={"raw": True},
        message_type="text_message",
        action_type="addChatItem",
    )

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda data, original_item, original_message_type, original_action_type: {
            "data": data,
            "item": original_item,
            "message_type": original_message_type,
            "action_type": original_action_type,
        },
    )

    assert _validate_pipeline_message(parse_result) == {
        "data": {"message": "hello"},
        "item": {"raw": True},
        "message_type": "text_message",
        "action_type": "addChatItem",
    }


def test_check_time_filter_defaults_to_yield_without_filter() -> None:
    assert _check_time_filter({"message": "hello"}, None) == "yield"


def test_check_time_filter_propagates_skip_and_stop() -> None:
    skip_filter = _DummyTimeFilter("skip")
    stop_filter = _DummyTimeFilter("stop")
    message = {"message": "hello"}

    assert _check_time_filter(message, cast("TimeRangeFilter", skip_filter)) == "skip"
    assert _check_time_filter(message, cast("TimeRangeFilter", stop_filter)) == "stop"
    assert skip_filter.seen_messages == [message]
    assert stop_filter.seen_messages == [message]


def test_process_pipeline_action_categorizes_unparsed_action(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda _action, _offset: None,
    )

    result = process_pipeline_action(
        {"raw": True},
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.UNPARSED_ACTION


def test_process_pipeline_action_categorizes_known_ignored_control_action() -> None:
    result = process_pipeline_action(
        {
            "replayChatItemAction": {
                "actions": [{"addInteractivityWidgetAction": {}}],
            },
        },
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.KNOWN_IGNORED_ACTION


def test_process_pipeline_action_categorizes_known_ignored_renderer() -> None:
    result = process_pipeline_action(
        {
            "addChatItemAction": {
                "item": {"liveChatPlaceholderItemRenderer": {}},
            },
        },
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.KNOWN_IGNORED_MESSAGE


def test_process_pipeline_action_skips_when_validation_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda _action, _offset: ProcessedAction(
            parsed_data={},
            original_item={"raw": True},
            message_type="text_message",
            action_type="addChatItem",
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda *_args, **_kwargs: None,
    )

    result = process_pipeline_action(
        {"raw": True},
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.INVALID_MESSAGE


def test_process_pipeline_action_skips_when_message_filter_rejects(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda _action, _offset: ProcessedAction(
            parsed_data={},
            original_item={"raw": True},
            message_type="text_message",
            action_type="addChatItem",
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda *_args, **_kwargs: {"message": "hello"},
    )
    msg_filter = _DummyMessageFilter(should_add_result=False)

    result = process_pipeline_action(
        {"raw": True},
        0,
        cast("MessageFilter", msg_filter),
        None,
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.MESSAGE_FILTERED
    assert msg_filter.seen_messages == [{"message": "hello"}]


def test_process_pipeline_action_skips_when_time_filter_skips(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda _action, _offset: ProcessedAction(
            parsed_data={},
            original_item={"raw": True},
            message_type="text_message",
            action_type="addChatItem",
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda *_args, **_kwargs: {"message": "hello"},
    )
    time_filter = _DummyTimeFilter("skip")

    result = process_pipeline_action(
        {"raw": True},
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        cast("TimeRangeFilter", time_filter),
    )

    assert result.disposition == "skip"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.TIME_RANGE_FILTERED
    assert time_filter.seen_messages == [{"message": "hello"}]


def test_process_pipeline_action_stops_when_time_filter_stops(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda _action, _offset: ProcessedAction(
            parsed_data={},
            original_item={"raw": True},
            message_type="text_message",
            action_type="addChatItem",
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda *_args, **_kwargs: {"message": "hello"},
    )

    result = process_pipeline_action(
        {"raw": True},
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        cast("TimeRangeFilter", _DummyTimeFilter("stop")),
    )

    assert result.disposition == "stop"
    assert result.message is None
    assert result.non_emission_reason is NonEmissionReason.TIME_RANGE_STOPPED


def test_process_pipeline_action_yields_valid_message(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.process_action",
        lambda action, offset: ProcessedAction(
            parsed_data={"message": f"{action['raw']}:{offset}"},
            original_item={"raw": True},
            message_type="text_message",
            action_type="addChatItem",
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.message_pipeline.validate_and_finalize_message",
        lambda data, *_args, **_kwargs: data,
    )

    result = process_pipeline_action(
        {"raw": "hello"},
        5,
        cast("MessageFilter", _DummyMessageFilter()),
        cast("TimeRangeFilter", _DummyTimeFilter(None)),
    )

    assert result.disposition == "yield"
    assert result.message == {"message": "hello:5"}
    assert result.non_emission_reason is None


def test_process_pipeline_action_tolerates_thumbnail_shape_drift() -> None:
    result = process_pipeline_action(
        {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": "message-1",
                        "timestampUsec": "1700000000000000",
                        "authorName": {"simpleText": "Author"},
                        "authorPhoto": {
                            "thumbnails": [
                                {
                                    "url": "https://img.example/avatar=s32",
                                    "width": 32,
                                    "height": 32,
                                    "newField": "ignored",
                                },
                                {"width": 64, "height": 64},
                            ]
                        },
                        "message": {"runs": [{"text": "hello"}]},
                    }
                }
            }
        },
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "yield"
    assert result.message is not None
    assert result.message["author"]["images"] == [
        {"url": "https://img.example/avatar", "id": "source"},
        {
            "url": "https://img.example/avatar=s32",
            "width": 32,
            "height": 32,
            "id": "32x32",
        },
    ]


def test_process_pipeline_action_tolerates_malformed_emoji() -> None:
    result = process_pipeline_action(
        {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": "message-1",
                        "timestampUsec": "1700000000000000",
                        "authorName": {"simpleText": "Author"},
                        "message": {"runs": [{"emoji": None}]},
                    }
                }
            }
        },
        0,
        cast("MessageFilter", _DummyMessageFilter()),
        None,
    )

    assert result.disposition == "yield"
    assert result.message is not None
    assert result.message["message"] == ":emoji:"

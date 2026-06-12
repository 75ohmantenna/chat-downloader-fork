# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.sites.youtube.message_pipeline import (
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


def test_process_pipeline_action_skips_when_action_is_ignored(
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

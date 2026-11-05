# SPDX-License-Identifier: MIT

"""Synthetic replay cardinality regressions; not evidence of provider traffic."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader import run
from chat_downloader.sites.filters import MessageFilter, TimeRangeFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuation_helpers import ContinuationLoopState
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.message_pipeline import (
    _process_actions,
    process_pipeline_action,
)
from chat_downloader.sites.youtube.parsing.actions_router import (
    iter_action_occurrences,
    process_action,
)
from tests.youtube_third_helpers import http_response, video_info

_FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/continuations/synthetic-multi-inner-replay.json"
)


def _payload():
    return json.loads(_FIXTURE.read_text())


def _raw_actions(payload):
    return payload["continuationContents"]["liveChatContinuation"]["actions"]


def _messages(actions, diagnostics, *, offset=0, time_filter=None):
    return _process_actions(
        actions,
        offset,
        MessageFilter(_MESSAGE_GROUPS, types_to_add=["text_message"]),
        time_filter,
        ContinuationLoopState(continuation="synthetic"),
        0,
        is_replay=True,
        diagnostics=diagnostics,
    )


def _identity(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def test_replay_siblings_preserve_order_occurrences_and_loss_accounting():
    payload = _payload()
    before = deepcopy(payload)
    identity = _identity(payload)
    diagnostics = {}
    messages = list(_messages(_raw_actions(payload), diagnostics))
    assert [message["message_id"] for message in messages] == [
        "repeated",
        "repeated",
        "last",
    ]
    assert [message["time_in_seconds"] for message in messages] == [12.345] * 3
    assert diagnostics == {
        "poll_count": 1,
        "processed_action_count": 7,
        "emitted_message_count": 3,
        "non_emission_counts": {
            "known ignored/control actions": 1,
            "message type/group filtered": 1,
            "unparsed actions": 1,
            "invalid messages": 1,
        },
        "parse_error": 2,
        "replay_timestamp_missing_count": 3,
        "replay_zero_offset_without_timestamp_count": 0,
    }
    assert payload == before
    assert _identity(payload) == identity


@pytest.mark.parametrize(
    ("wrapper_time", "expected"),
    [
        ("12345", [10.345] * 3),
        ("0", [-1, -1, 0]),
        ("nan", [-1, -1, 0]),
        (None, [-1, -1, 0]),
    ],
)
def test_each_sibling_inherits_only_valid_wrapper_time_and_one_clip_offset(
    wrapper_time, expected
):
    actions = _raw_actions(_payload())
    wrapper = actions[0]["replayChatItemAction"]
    if wrapper_time is None:
        wrapper.pop("videoOffsetTimeMsec")
    else:
        wrapper["videoOffsetTimeMsec"] = wrapper_time
    messages = list(_messages(actions, {}, offset=2))
    assert [message["time_in_seconds"] for message in messages] == pytest.approx(
        expected
    )


def test_sibling_time_filter_is_independent():
    actions = _raw_actions(_payload())
    actions[0]["replayChatItemAction"].pop("videoOffsetTimeMsec")
    diagnostics = {}
    messages = list(
        _messages(
            actions,
            diagnostics,
            time_filter=TimeRangeFilter(1.5, 3, skip_mode="always"),
        )
    )
    assert [message["message_id"] for message in messages] == ["last"]
    assert diagnostics["non_emission_counts"]["time-range filtered"] == 2
    assert diagnostics["processed_action_count"] == 7


@pytest.mark.parametrize("stop", [False, True])
def test_sibling_processing_retains_consumed_prefix_on_close_or_time_stop(stop):
    actions = _raw_actions(_payload())
    diagnostics = {}
    generator = _messages(
        actions,
        diagnostics,
        time_filter=TimeRangeFilter(0, 12) if stop else None,
    )
    if stop:
        with pytest.raises(StopIteration) as exc:
            next(generator)
        assert exc.value.value is True
        assert diagnostics["non_emission_counts"] == {"time-range stop": 1}
    else:
        assert next(generator)["message_id"] == "repeated"
        generator.close()
    assert diagnostics["processed_action_count"] == 1
    assert diagnostics["emitted_message_count"] == (0 if stop else 1)
    assert "parse_error" not in diagnostics


def test_singular_parsers_reject_multi_inner_input_instead_of_losing_siblings():
    action = _raw_actions(_payload())[0]
    with pytest.raises(ValueError, match="iter_action_occurrences"):
        process_action(action)
    with pytest.raises(ValueError, match="iter_action_occurrences"):
        process_pipeline_action(action, 0, MessageFilter(_MESSAGE_GROUPS), None)


@pytest.mark.parametrize("action", [{}, {"clickTrackingParams": "synthetic"}])
def test_singular_metadata_only_actions_are_unparsed_without_mutation(action):
    before = deepcopy(action)
    assert process_action(action) is None
    assert list(iter_action_occurrences([action])) == [before]
    assert action == before


@pytest.mark.parametrize("route", ["watch?v=abcdefghijk", "clip/synthetic"])
def test_multi_inner_replay_composes_durable_output_and_parser_loss(
    tmp_path, monkeypatch, route
):
    payload = _payload()
    before = deepcopy(payload)
    _patch_replay(monkeypatch, payload)
    result = run(
        url=f"https://www.youtube.com/{route}",
        message_types=["text_message"],
        quiet=True,
        verify_output=True,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        run_manifest=str(tmp_path / "run.json"),
    )
    assert not result.success  # Matching files cannot hide malformed siblings.
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "review"
    assert result.provider_inspection["issues"]["duplicate_text_message_id"] == {
        "count": 1,
        "first_line": 2,
    }
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [row["message_id"] for row in rows] == ["repeated", "repeated", "last"]
    assert [row["time_in_seconds"] for row in rows] == [12.345] * 3
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 3
    counters = result.provider_inspection["run_accounting"]["counters"]
    assert counters["parse_error"] == 2
    assert result.provider_diagnostics["processed_action_count"] == 7
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection
    assert payload == before


def _patch_replay(monkeypatch, payload):
    def initial(_site, _video_id, _params, video_type="video"):
        extra = (
            {"clip_start_time": 0, "clip_end_time": 30} if video_type == "clip" else {}
        )
        return video_info("was_live", duration=30, **extra), {
            "INNERTUBE_API_KEY": "synthetic"
        }

    monkeypatch.setattr(YouTubeChatDownloader, "_get_initial_video_info", initial)
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_post",
        lambda *_args, **_kwargs: http_response(payload=payload),
    )


def test_checkpoint_resume_retains_same_identity_distinct_action_kinds(
    tmp_path, monkeypatch
):
    payload = _payload()
    wrapper = _raw_actions(payload)[0]["replayChatItemAction"]
    wrapper["actions"] = [wrapper["actions"][index] for index in (0, 5, 6)]
    repeated_item = wrapper["actions"][1]["addChatItemAction"]["item"]
    wrapper["actions"][1] = {
        "replaceChatItemAction": {"replacementItem": repeated_item}
    }
    _patch_replay(monkeypatch, payload)
    params = {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "message_types": ["text_message"],
        "quiet": True,
        "verify_output": True,
        "output": [str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        "resume": str(tmp_path / "checkpoint.json"),
    }
    first = run(**params, max_messages=1)
    assert first.success
    assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == 1
    resumed = run(**params, require_complete=True)
    assert resumed.success
    assert resumed.parity_status == "passed"
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [row["message_id"] for row in rows] == ["repeated", "repeated", "last"]
    assert [row["action_type"] for row in rows] == [
        "add_chat_item",
        "replace_chat_item",
        "add_chat_item",
    ]
    assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == 3


def test_tracking_metadata_does_not_hide_moderation_event_or_mutate_input():
    action = {
        "clickTrackingParams": "synthetic",
        "removeChatItemByAuthorAction": {"externalChannelId": "synthetic-author"},
    }
    before = deepcopy(action)
    result = process_pipeline_action(
        action, 0, MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"]), None
    )
    assert result.message["message_type"] == "ban_user"
    assert result.message["action_type"] == "remove_chat_item_by_author"
    assert result.message["author"]["id"] == "synthetic-author"
    assert action == before


@pytest.mark.parametrize(
    "nested_actions",
    [
        [{"addChatItemAction": {"item": {}}}],
        [{"addChatItemAction": {"item": {}}}, {"addInteractivityWidgetAction": {}}],
    ],
)
def test_nested_replay_sibling_counts_loss_without_losing_following_action(
    nested_actions,
):
    actions = _raw_actions(_payload())
    wrapper = actions[0]["replayChatItemAction"]
    wrapper["actions"] = [
        wrapper["actions"][0],
        {"replayChatItemAction": {"actions": nested_actions}},
        wrapper["actions"][-1],
    ]
    before = deepcopy(actions)
    diagnostics = {}
    messages = list(_messages(actions, diagnostics))
    assert [message["message_id"] for message in messages] == ["repeated", "last"]
    assert diagnostics["processed_action_count"] == 3
    assert diagnostics["parse_error"] == 1
    assert diagnostics["non_emission_counts"] == {"unparsed actions": 1}
    assert actions == before


@pytest.mark.parametrize(
    ("action_type", "item_key"),
    [
        ("addChatItemAction", "item"),
        ("addLiveChatTickerItemAction", "item"),
        ("replaceChatItemAction", "replacementItem"),
        ("showLiveChatTooltipCommand", "tooltip"),
    ],
)
@pytest.mark.parametrize("item", [None, False, 1, "malformed", []])
def test_malformed_item_sibling_counts_loss_without_losing_following_action(
    action_type, item_key, item
):
    actions = _raw_actions(_payload())
    wrapper = actions[0]["replayChatItemAction"]
    wrapper["actions"] = [
        wrapper["actions"][0],
        {action_type: {item_key: item}},
        wrapper["actions"][-1],
    ]
    before = deepcopy(actions)
    diagnostics = {}
    messages = list(_messages(actions, diagnostics))
    assert [message["message_id"] for message in messages] == ["repeated", "last"]
    assert diagnostics["processed_action_count"] == 3
    assert diagnostics["parse_error"] == 1
    assert diagnostics["non_emission_counts"] == {"invalid messages": 1}
    assert actions == before

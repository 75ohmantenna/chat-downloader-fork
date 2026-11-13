# SPDX-License-Identifier: MIT

"""Captured commerce UI controls must not hide unknown elements commands."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader import run
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.continuation_helpers import ContinuationLoopState
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    _process_actions,
    process_pipeline_action,
)
from chat_downloader.sites.youtube.parsing.actions_router import (
    is_known_ignored_action,
    process_action,
)
from tests.youtube_third_helpers import http_response, item_action, response, video_info

FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/live_events/youtube-commerce-balance-control.json"
)


def _control():
    payload = json.loads(FIXTURE.read_text())
    return payload["continuationContents"]["liveChatContinuation"]["actions"][0]


def _filter():
    return MessageFilter(_MESSAGE_GROUPS, types_to_add=["text_message"])


def _text():
    return item_action(
        "liveChatTextMessageRenderer",
        {
            "id": "neighbor",
            "timestampUsec": "1000000",
            "message": {"runs": [{"text": "Neighboring chat message"}]},
        },
    )


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("variant", ["captured", "rotated_hashes", "no_tracking"])
def test_captured_balance_control_is_ignored_without_drift_or_mutation(
    monkeypatch, replay, variant
):
    action = _control()
    if variant == "rotated_hashes":
        command = action["elementsCommand"]["executeJsFunctionCommand"]
        command["functionName"] = "updateCommerceTokenBalance|0123456789abcdef"
        command["moduleIdentifier"] = "update_token_balance_command|fedcba9876543210"
    elif variant == "no_tracking":
        del action["clickTrackingParams"]
    if replay:
        action = {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "1000",
                "actions": [action],
            }
        }
    before = deepcopy(action)
    drift = Mock()
    sample = Mock()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.debug_log", drift
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        sample,
    )
    assert is_known_ignored_action(action)
    assert process_action(action) is None
    result = process_pipeline_action(action, 0, _filter(), None)
    assert result.disposition == "skip"
    assert result.non_emission_reason is NonEmissionReason.KNOWN_IGNORED_ACTION
    assert result.message is None
    assert action == before
    drift.assert_not_called()
    sample.assert_not_called()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("elementsCommand", None),
        ("elementsCommand", []),
        ("elementsCommand.executeJsFunctionCommand", "not an object"),
        ("elementsCommand.executeJsFunctionCommand.functionName", None),
        ("elementsCommand.executeJsFunctionCommand.functionName", 1),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalance",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalanceExtra|7d546428e27fca8a",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalance|",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalance|7d546428e27fca8",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalance|7D546428E27FCA8A",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.functionName",
            "updateCommerceTokenBalance|7d546428e27fca8a\n",
        ),
        ("elementsCommand.executeJsFunctionCommand.moduleIdentifier", None),
        ("elementsCommand.executeJsFunctionCommand.moduleIdentifier", []),
        (
            "elementsCommand.executeJsFunctionCommand.moduleIdentifier",
            "another_module|76b9c5a70d918300",
        ),
        (
            "elementsCommand.executeJsFunctionCommand.moduleIdentifier",
            "update_token_balance_command|76b9c5a70d918300extra",
        ),
        ("elementsCommand.executeJsFunctionCommand.data", None),
        ("elementsCommand.executeJsFunctionCommand.data", []),
        ("elementsCommand.executeJsFunctionCommand.data.functionArguments", ""),
        ("elementsCommand.executeJsFunctionCommand.data.functionArguments", None),
        ("elementsCommand.executeJsFunctionCommand.data.functionArguments", 0),
        ("elementsCommand.executeJsFunctionCommand.data.functionArguments", {}),
        ("elementsCommand.executeJsFunctionCommand.data.functionArguments", []),
        ("elementsCommand.extraCommand", {}),
        ("elementsCommand.executeJsFunctionCommand.extraCommand", {}),
        ("elementsCommand.executeJsFunctionCommand.data.extraArgument", {}),
        (
            "addChatItemAction",
            {"item": {"liveChatTextMessageRenderer": {"id": "hidden"}}},
        ),
    ],
)
def test_unrecognized_or_malformed_elements_commands_remain_unparsed_and_sampled(
    monkeypatch, path, value
):
    action = _control()
    target = action
    *parents, key = path.split(".")
    for parent in parents:
        target = target[parent]
    target[key] = value
    before = deepcopy(action)
    sample = Mock()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        sample,
    )
    assert not is_known_ignored_action(action)
    result = process_pipeline_action(action, 0, _filter(), None)
    assert result.non_emission_reason is NonEmissionReason.UNPARSED_ACTION
    assert action == before
    assert sample.call_args.args[0] == "youtube-unknown-action-elementsCommand"
    assert (
        sample.call_args.args[1]["action"]["elementsCommand"]
        == action["elementsCommand"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "elementsCommand.executeJsFunctionCommand",
        "elementsCommand.executeJsFunctionCommand.functionName",
        "elementsCommand.executeJsFunctionCommand.moduleIdentifier",
        "elementsCommand.executeJsFunctionCommand.data",
        "elementsCommand.executeJsFunctionCommand.data.functionArguments",
    ],
)
def test_missing_control_fields_remain_unparsed(path):
    action = _control()
    target = action
    *parents, key = path.split(".")
    for parent in parents:
        target = target[parent]
    del target[key]
    assert not is_known_ignored_action(action)
    result = process_pipeline_action(action, 0, _filter(), None)
    assert result.non_emission_reason is NonEmissionReason.UNPARSED_ACTION


def test_nested_replay_wrappers_remain_unparsed_and_sampled(monkeypatch):
    inner = {"replayChatItemAction": {"actions": [_control()]}}
    action = {"replayChatItemAction": {"actions": [inner]}}
    before = deepcopy(action)
    sample = Mock()
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_router.capture_debug_sample",
        sample,
    )
    assert not is_known_ignored_action(action)
    result = process_pipeline_action(action, 0, _filter(), None)
    assert result.non_emission_reason is NonEmissionReason.UNPARSED_ACTION
    assert action == before
    assert sample.call_args.args[0] == "youtube-unknown-action-replayChatItemAction"


@pytest.mark.parametrize("replay", [False, True])
def test_balance_control_keeps_neighboring_messages_and_unknown_loss_accounting(replay):
    unknown = _control()
    unknown["elementsCommand"]["executeJsFunctionCommand"]["functionName"] = "other"
    actions = [_control(), _text(), unknown]
    if replay:
        actions = [
            {
                "replayChatItemAction": {
                    "videoOffsetTimeMsec": "1000",
                    "actions": actions,
                }
            }
        ]
    before = deepcopy(actions)
    diagnostics = {}
    messages = list(
        _process_actions(
            actions,
            0,
            _filter(),
            None,
            ContinuationLoopState(continuation="fixture"),
            0,
            is_replay=replay,
            diagnostics=diagnostics,
        )
    )
    assert [message["message_id"] for message in messages] == ["neighbor"]
    assert diagnostics["processed_action_count"] == 3
    assert diagnostics["emitted_message_count"] == 1
    assert diagnostics["parse_error"] == 1
    assert diagnostics["non_emission_counts"] == {
        "known ignored/control actions": 1,
        "unparsed actions": 1,
    }
    assert actions == before


@pytest.mark.parametrize("status", ["live", "was_live"])
@pytest.mark.parametrize("view", ["live", "top"])
@pytest.mark.parametrize("known", [True, False])
def test_balance_control_verification_through_real_polling_writers_and_manifest(
    tmp_path, monkeypatch, status, view, known
):
    action = _control()
    if not known:
        action["elementsCommand"]["executeJsFunctionCommand"]["moduleIdentifier"] = (
            "unknown"
        )
    actions = [action, _text()]
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_get_initial_video_info",
        lambda *_args, **_kwargs: (
            video_info(status, duration=30),
            {"INNERTUBE_API_KEY": "fixture"},
        ),
    )
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_post",
        lambda *_args, **_kwargs: http_response(payload=response(deepcopy(actions))),
    )
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        request_profile="youtube_android",
        chat_type=view,
        message_types=["text_message"],
        quiet=True,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        verify_output=True,
        run_manifest=str(tmp_path / "run.json"),
    )
    assert result.success is known
    assert result.message_count == 1
    assert result.parity_status == "passed"
    assert result.provider_diagnostics.get("parse_error", 0) == int(not known)
    assert result.provider_inspection["status"] == ("ok" if known else "review")
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection
    assert manifest["provider_diagnostics"] == result.provider_diagnostics
    assert json.loads((tmp_path / "chat.jsonl").read_text())["message_id"] == "neighbor"
    assert "Neighboring chat message" in (tmp_path / "chat.txt").read_text()

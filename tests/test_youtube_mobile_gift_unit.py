# SPDX-License-Identifier: MIT

"""Captured mobile gift wrappers preserve content and replay identity."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader import run
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action
from chat_downloader.sites.youtube.parsing.modern_elements import normalize_element
from tests.youtube_third_helpers import http_response, response, video_info

FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/live_events/youtube-mobile-wrapped-gift.json"
)


def _action():
    return json.loads(FIXTURE.read_text())["continuationContents"][
        "liveChatContinuation"
    ]["actions"][0]


def _item():
    return _action()["addChatItemAction"]["item"]


@pytest.mark.parametrize("replay", [False, True])
def test_captured_wrapped_gift_preserves_content_identity_and_replay_timing(replay):
    action = _action()
    if replay:
        action = {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "12345",
                "actions": [action],
            }
        }
    before = deepcopy(action)
    result = process_pipeline_action(
        action,
        0,
        MessageFilter(_MESSAGE_GROUPS, types_to_add=["gift_message_view_model"]),
        None,
    )
    assert result.disposition == "yield"
    message = result.message
    assert message["message_type"] == "gift_message_view_model"
    assert message["message_id"] == "mobile-gift-1"
    assert message["author"]["name"] == "@FixtureGifter "
    assert message["author"]["id"] == "UCfixture-gifter"
    assert message["message"] == "sent Go team! for 5 Jewels"
    assert "timestamp" not in message
    assert "received_timestamp" not in message
    if replay:
        assert message["time_in_seconds"] == pytest.approx(12.345)
    else:
        assert "time_in_seconds" not in message
    assert action == before


def test_mobile_gift_logging_identifier_never_becomes_a_source_timestamp():
    item = _item()
    normalized = normalize_element(item)
    item["elementRenderer"]["newElement"]["properties"]["identifierProperties"][
        "uniqueLoggingIdentifier"
    ] = "9999999999999999999"
    assert normalize_element(item) == normalized
    assert "timestampUsec" not in normalized["giftMessageViewModel"]


@pytest.mark.parametrize("compatibility", [{}, {"liveChatId": "mobile-gift-1"}])
def test_mobile_gift_retains_inner_identity_without_compatibility_metadata(
    compatibility,
):
    item = _item()
    item["elementRenderer"]["compatibilityOptions"] = compatibility
    normalized = normalize_element(item)
    assert normalized["giftMessageViewModel"]["id"] == "mobile-gift-1"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("context", "malformed context"),
        ("unrecognizedModel", {}),
        ("viewModel", None),
        ("viewModel", []),
        ("viewModel", {}),
        ("viewModel.textMessageViewModel", {}),
        ("viewModel.giftMessageViewModel", None),
        ("viewModel.giftMessageViewModel.id", None),
        ("viewModel.giftMessageViewModel.id", ""),
        ("viewModel.giftMessageViewModel.id", "  "),
        ("viewModel.giftMessageViewModel.authorName", {}),
        ("viewModel.giftMessageViewModel.authorName.content", []),
        ("viewModel.giftMessageViewModel.authorName.content", " "),
        ("viewModel.giftMessageViewModel.text", {}),
        ("viewModel.giftMessageViewModel.text.content", None),
        ("viewModel.giftMessageViewModel.text.content", ""),
    ],
)
def test_unknown_or_malformed_wrapped_gifts_remain_diagnosable(path, value):
    item = _item()
    target = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "youtubeModel"
    ]
    *parents, key = path.split(".")
    for parent in parents:
        target = target[parent]
    target[key] = value
    before = deepcopy(item)
    assert normalize_element(item) is item
    assert item == before


def test_mobile_gift_rejects_conflicting_wrapper_and_message_identity():
    item = _item()
    item["elementRenderer"]["compatibilityOptions"]["liveChatId"] = "other-message"
    assert normalize_element(item) is item


def test_mobile_gift_rejects_conflicting_wrapper_and_author_identity():
    item = _item()
    model = item["elementRenderer"]["newElement"]["type"]["componentType"]["model"]
    model["youtubeModel"]["viewModel"]["giftMessageViewModel"][
        "authorExternalChannelId"
    ] = "other-author"
    before = deepcopy(item)
    assert normalize_element(item) is item
    assert item == before


def test_mixed_outer_renderers_remain_unchanged_and_diagnosable(monkeypatch):
    item = _item()
    item["liveChatTextMessageRenderer"] = {
        "id": "other-message",
        "message": {"runs": [{"text": "Other message"}]},
    }
    before = deepcopy(item)
    samples = []
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.actions_handlers_validation.capture_debug_sample",
        lambda *args, **_kwargs: samples.append(args),
    )
    assert normalize_element(item) is item
    result = process_pipeline_action(
        {"addChatItemAction": {"item": item}},
        0,
        MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"]),
        None,
    )
    assert result.message["message_type"] == "element"
    assert samples[-1][0] == "youtube-unknown-message-type-elementRenderer"
    assert item == before


@pytest.mark.parametrize("timestamp", [None, "1234567890"])
@pytest.mark.parametrize("replay", [False, True])
def test_wrapped_gift_matches_native_gift_without_losing_optional_fields(
    timestamp, replay
):
    wrapped = _action()
    element = wrapped["addChatItemAction"]["item"]["elementRenderer"]
    gift = element["newElement"]["type"]["componentType"]["model"]["youtubeModel"][
        "viewModel"
    ]["giftMessageViewModel"]
    if timestamp is not None:
        gift["timestampUsec"] = timestamp
    native_gift = {**deepcopy(gift), "authorExternalChannelId": "UCfixture-gifter"}
    native = {"addChatItemAction": {"item": {"giftMessageViewModel": native_gift}}}
    if replay:
        wrapped = {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "12000",
                "actions": [wrapped],
            }
        }
        native = {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "12000",
                "actions": [native],
            }
        }
    msg_filter = MessageFilter(
        _MESSAGE_GROUPS, types_to_add=["gift_message_view_model"]
    )
    assert process_pipeline_action(wrapped, 0, msg_filter, None).message == (
        process_pipeline_action(native, 0, msg_filter, None).message
    )


@pytest.mark.parametrize("status", ["live", "was_live"])
@pytest.mark.parametrize("view", ["live", "top"])
def test_mobile_gift_verifies_through_polling_files_and_manifest(
    tmp_path, monkeypatch, status, view
):
    action = _action()
    if status == "was_live":
        action = {
            "replayChatItemAction": {"videoOffsetTimeMsec": "1000", "actions": [action]}
        }
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
        lambda *_args, **_kwargs: http_response(payload=response([deepcopy(action)])),
    )
    result = run(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        request_profile="youtube_android",
        chat_type=view,
        message_types=["gift_message_view_model"],
        quiet=True,
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        verify_output=True,
        run_manifest=str(tmp_path / "run.json"),
    )
    assert result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "ok"
    assert result.message_type_counts == {"gift_message_view_model": 1}
    assert result.provider_diagnostics.get("parse_error", 0) == 0
    stored = json.loads((tmp_path / "chat.jsonl").read_text())
    assert stored["message_type"] == "gift_message_view_model"
    assert stored["message"] == "sent Go team! for 5 Jewels"
    assert "sent Go team! for 5 Jewels" in (tmp_path / "chat.txt").read_text()
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection

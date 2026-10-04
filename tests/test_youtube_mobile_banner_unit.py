# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_downloader.debugging import TestingModes as _TestingModes
from chat_downloader.debugging import (
    get_testing_mode,
    set_testing_mode,
)
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
from chat_downloader.sites.youtube.message_pipeline import (
    NonEmissionReason,
    process_pipeline_action,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/live_events/youtube-S7ugRA3UMjo-mobile-banner.json"
)


def _action():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return payload["continuationContents"]["liveChatContinuation"]["actions"][0]


def _parse(action):
    return process_pipeline_action(
        action, 0, MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"]), None
    )


def test_mobile_banner_preserves_content_without_ui_drift() -> None:
    previous = get_testing_mode()
    try:
        set_testing_mode(_TestingModes.EXIT_ON_DEBUG)
        result = _parse(_action())
    finally:
        set_testing_mode(previous)

    message = result.message
    assert result.disposition == "yield"
    assert message["message_type"] == "banner"
    assert message["message"] == "Pinned chat message 🙏"
    assert message["message_id"] == "fixture-mobile-banner"
    assert message["author"]["name"] == "@MPLIndonesia"
    assert message["author"]["id"] == "UC1dGHGJTXU_dkiR8tW3qQgg"
    assert message["author"]["is_verified"] is True
    assert "is_sponsor" not in message["author"]
    assert message["author"]["badges"] == [
        {"icon_name": "verified", "title": "Verified"}
    ]
    assert message["author"]["images"]
    assert "timestamp" not in message
    assert "time_in_seconds" not in message


@pytest.mark.parametrize("case", ["unknown", "empty", "author-only"])
def test_unsupported_mobile_banner_does_not_emit_empty_success(
    monkeypatch, case
) -> None:
    from chat_downloader.sites.youtube.parsing import actions_handlers_parser

    captured = []
    monkeypatch.setattr(
        actions_handlers_parser,
        "capture_debug_sample",
        lambda label, payload, **kwargs: captured.append((label, payload)),
    )
    action = _action()
    models = action["addBannerToLiveChatCommand"]["bannerRenderer"][
        "liveChatBannerRenderer"
    ]["contents"]["elementRenderer"]["newElement"]["type"]["componentType"]["model"]
    if case == "empty":
        models["liveChatTextMessageBannerModel"] = {}
    elif case == "author-only":
        models["liveChatTextMessageBannerModel"] = {
            "messageData": {"attributedTextData": {"authorName": {"content": "Author"}}}
        }
    else:
        models["unknownBannerModel"] = models.pop("liveChatTextMessageBannerModel")

    result = _parse(action)

    assert result.message is None
    assert result.non_emission_reason == NonEmissionReason.INVALID_MESSAGE
    assert captured[0][0] == "youtube-unsupported-banner-content"


@pytest.mark.parametrize(
    "renderer",
    [
        None,
        [],
        "invalid",
        {},
        {"liveChatBannerRenderer": None},
        {"liveChatBannerRenderer": "invalid"},
    ],
)
def test_malformed_banner_wrapper_is_accounted_instead_of_crashing(renderer):
    action = _action()
    action["addBannerToLiveChatCommand"]["bannerRenderer"] = renderer
    result = _parse(action)
    assert result.message is None
    assert result.non_emission_reason == NonEmissionReason.INVALID_MESSAGE

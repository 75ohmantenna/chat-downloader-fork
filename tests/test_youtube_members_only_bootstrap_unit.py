# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import CaptchaChallengeRequired, VideoUnavailable
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.youtube_third_helpers import http_response, submenu_item, wrap

_VIDEO_ID = "example0001"
_URL = f"https://www.youtube.com/watch?v={_VIDEO_ID}"
_FIXTURE = (
    Path(__file__).parent
    / "fixtures/youtube/innertube_bootstrap/members-only-watch.json"
)


@pytest.fixture
def watch():
    return json.loads(_FIXTURE.read_text())


def _install_watch(monkeypatch, watch, *responses):
    player = json.dumps(watch["player_response"])
    data = json.dumps(watch["initial_data"])
    response = http_response(
        text=f"<script>var ytInitialPlayerResponse = {player};</script>"
        f"<script>var ytInitialData = {data};</script>"
    )
    get = Mock(side_effect=[response, *responses])
    monkeypatch.setattr(YouTubeChatDownloader, "_session_get", get)
    return get


def _watch_chat(watch):
    conversation = watch["initial_data"]["contents"]["twoColumnWatchNextResults"][
        "conversationBar"
    ]
    return conversation.setdefault("liveChatRenderer", {})


@pytest.mark.parametrize("reload_token", [None, "  ", {"invalid": True}])
def test_members_only_run_reports_bootstrap_failure_without_pausing(
    monkeypatch, caplog, watch, reload_token
):
    if reload_token is not None:
        _watch_chat(watch)["continuations"] = [
            {"reloadContinuationData": {"continuation": reload_token}}
        ]
    get = _install_watch(monkeypatch, watch)
    post = Mock()
    monkeypatch.setattr(YouTubeChatDownloader, "_session_post", post)
    pause = Mock()
    monkeypatch.setattr("chat_downloader.debugging.pause", pause)

    result = execute_run(
        ChatDownloader,
        url=_URL,
        auto_profile_fallback=True,
        pause_on_debug=True,
    )

    assert not result.success
    assert result.message_count == 0
    assert result.failure_stage == "bootstrap"
    assert (
        result.error_message == watch["player_response"]["playabilityStatus"]["reason"]
    )
    assert result.provider_diagnostics["bootstrap_request_count"] == 1
    assert result.provider_diagnostics["bootstrap_profile_switch_count"] == 0
    get.assert_called_once_with(_URL, allow_redirects=True)
    post.assert_not_called()
    pause.assert_not_called()
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


def test_members_only_offer_yields_to_player_captcha(monkeypatch, watch):
    playability = watch["player_response"]["playabilityStatus"]
    playability["errorScreen"]["playerCaptchaViewModel"] = {}
    get = _install_watch(monkeypatch, watch)

    with (
        closing(YouTubeChatDownloader()) as downloader,
        pytest.raises(CaptchaChallengeRequired, match="CAPTCHA verification"),
    ):
        downloader._get_initial_video_info(_VIDEO_ID)
    get.assert_called_once_with(_URL, allow_redirects=True)


def test_members_only_offer_preserves_age_gate_precedence(monkeypatch, watch):
    watch["player_response"]["playabilityStatus"]["desktopLegacyAgeGateReason"] = (
        "confirm your age"
    )
    get = _install_watch(monkeypatch, watch)

    with (
        closing(YouTubeChatDownloader()) as downloader,
        pytest.raises(VideoUnavailable, match="age-restricted"),
    ):
        downloader._get_initial_video_info(_VIDEO_ID)
    get.assert_called_once_with(_URL, allow_redirects=True)


def test_other_offer_keeps_generic_profile_fallback(monkeypatch, caplog, watch):
    playability = watch["player_response"]["playabilityStatus"]
    playability["reason"] = "Video unavailable"
    offer = playability["errorScreen"]["playerLegacyDesktopYpcOfferRenderer"]
    offer["offerId"] = "rental"
    get = _install_watch(monkeypatch, watch)
    # Stop at the switch to test the policy without mocking mobile responses.
    switch_profile = Mock(return_value=False)
    monkeypatch.setattr(YouTubeChatDownloader, "apply_request_profile", switch_profile)

    result = execute_run(ChatDownloader, url=_URL, auto_profile_fallback=True)

    assert result.error_message == "Video unavailable"
    assert "Unable to enrich chat submenu" in caplog.text
    switch_profile.assert_called_once_with("youtube_android")
    get.assert_called_once_with(_URL, allow_redirects=True)


@pytest.mark.parametrize("reload", [False, True])
def test_members_only_offer_preserves_continuation_bootstrap(
    monkeypatch, watch, reload
):
    renderer = wrap(
        "header.liveChatHeaderRenderer.viewSelector.sortFilterSubMenuRenderer.subMenuItems",
        [
            submenu_item("top-replay", title="Top chat"),
            submenu_item("all-replay", title="Live chat"),
        ],
    )
    if reload:
        _watch_chat(watch)["continuations"] = [
            {"reloadContinuationData": {"continuation": "replay-token"}}
        ]
        response = wrap("continuationContents.liveChatContinuation", renderer)
        get = _install_watch(
            monkeypatch,
            watch,
            http_response(
                text=f"<script>var ytInitialData = {json.dumps(response)};</script>"
            ),
        )
    else:
        _watch_chat(watch).update(renderer)
        get = _install_watch(monkeypatch, watch)

    with closing(YouTubeChatDownloader()) as downloader:
        details, _ = downloader._get_initial_video_info(_VIDEO_ID)

    expected = {
        "Top chat replay" if reload else "Top chat": "top-replay",
        "Live chat replay" if reload else "Live chat": "all-replay",
    }
    assert details["continuation_info"] == expected
    assert get.call_count == (2 if reload else 1)


def test_members_only_offer_surfaces_reason_after_unsuccessful_chat_page(
    monkeypatch, watch
):
    playability = watch["player_response"]["playabilityStatus"]
    description = playability["errorScreen"]["playerLegacyDesktopYpcOfferRenderer"][
        "offerDescription"
    ]
    del playability["reason"]
    _watch_chat(watch)["continuations"] = [
        {"reloadContinuationData": {"continuation": "replay-token"}}
    ]
    get = _install_watch(monkeypatch, watch, http_response(text="<html></html>"))

    result = execute_run(
        ChatDownloader,
        url=_URL,
        max_attempts=1,
        auto_profile_fallback=True,
        exit_on_debug=True,
    )

    assert result.error_message == description
    assert result.failure_stage == "bootstrap"
    assert result.provider_diagnostics["bootstrap_profile_switch_count"] == 0
    assert get.call_count == 2

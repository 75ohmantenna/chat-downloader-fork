# SPDX-License-Identifier: MIT

"""Live provider contract checks and their scenario runner."""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, Mock

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    LoginRequired,
    VideoUnavailable,
)
from chat_downloader.sites import (
    BaseChatDownloader,
    KickChatDownloader,
    TwitchChatDownloader,
    get_all_sites,
)
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.fixtures.extractor_tests import (
    BASE_EXTRACTOR_TESTS,
    KICK_EXTRACTOR_TESTS,
    TWITCH_EXTRACTOR_TESTS,
)
from tests.fixtures.youtube.extractor_tests import YOUTUBE_EXTRACTOR_TESTS

if TYPE_CHECKING:
    from collections.abc import Callable

_SITE_TESTS: dict[type[BaseChatDownloader], list[dict[str, Any]]] = {
    BaseChatDownloader: BASE_EXTRACTOR_TESTS,
    KickChatDownloader: KICK_EXTRACTOR_TESTS,
    TwitchChatDownloader: TWITCH_EXTRACTOR_TESTS,
    YouTubeChatDownloader: YOUTUBE_EXTRACTOR_TESTS,
}


def _case_marks(test: dict[str, Any]) -> tuple[pytest.MarkDecorator, ...]:
    kind = (
        pytest.mark.network_live
        if test.get("network_live")
        else pytest.mark.network_replay
    )
    return pytest.mark.network, kind, pytest.mark.timeout(90)


_ALL_SITE_TESTS = [
    pytest.param(
        site,
        test,
        id=f"{site.__name__}-{test['name']}",
        marks=_case_marks(test),
    )
    for site in get_all_sites(include_parent=True)
    for test in _SITE_TESTS[site]
]


def _expected_errors(expected_result: dict[str, Any]) -> tuple[type[Exception], ...]:
    errors = expected_result.get("error")
    if errors is None:
        return ()
    if isinstance(errors, (list, tuple)):
        return tuple(errors)
    return (errors,)


def _run_site_integration(
    site: type[BaseChatDownloader],
    test: dict[str, Any],
    *,
    downloader_factory: Callable[[], Any] = ChatDownloader,
) -> None:
    """Check discovery via metadata; delivery additionally requires messages."""
    required_env = test.get("requires_env")
    selected_channel = os.environ.get(required_env) if required_env else None
    if required_env and not selected_channel:
        pytest.skip(
            f"Set {required_env} to an active public channel for live delivery."
        )
    params = dict(test["params"])
    assert params, "Network scenario must specify parameters."
    if required_env:
        params["url"] = params["url"].format(channel=selected_channel)
    expected_result = test.get("expected_result")
    assert expected_result, "Network scenario must declare an expected result."
    params.update({"max_attempts": 2, "interruptible_retry": False})

    expected_errors = _expected_errors(expected_result)
    downloader = downloader_factory()
    chat: Chat | None = None
    try:
        if expected_errors:
            try:
                chat = cast("Chat", downloader.get_chat(**params))
                list(chat)
            except CaptchaChallengeRequired:
                raise
            except expected_errors:
                return
            error_names = ", ".join(error.__name__ for error in expected_errors)
            raise AssertionError(f"Expected scenario to raise one of: {error_names}")

        chat = cast("Chat", downloader.get_chat(**params))
        if site is not BaseChatDownloader:
            assert chat.site is not None
            assert chat.site.__class__ is site

        chat_condition = expected_result.get("chat_condition")
        if chat_condition is not None:
            assert callable(chat_condition), "Chat check must be callable."
            assert chat_condition(chat), "Scenario metadata check failed."

        messages = list(chat)
        for message in messages:
            assert isinstance(message.get("message_type"), str)
            if message["message_type"] == "text_message":
                assert message.get("message_id")
                assert message.get("timestamp") is not None
                assert isinstance(message.get("author"), dict)
                assert isinstance(message.get("message"), str)
        messages_condition = expected_result.get("messages_condition")
        if messages_condition is not None:
            assert callable(messages_condition), "Message check must be callable."
            assert messages_condition(messages), (
                "Scenario message delivery check failed."
            )

        actual_types = {
            "message_types": {message.get("message_type") for message in messages},
            "action_types": {message.get("action_type") for message in messages},
        }
        checked_outcomes = {
            "chat_condition",
            "messages_condition",
            "message_types",
            "action_types",
        } & expected_result.keys()
        assert checked_outcomes, "Successful scenario must declare an outcome check."
        for key in ("message_types", "action_types"):
            if key in expected_result:
                assert set(expected_result[key]) == actual_types[key]
    finally:
        if chat is not None:
            chat.close()
        downloader.close()


@pytest.mark.parametrize(("site", "test"), _ALL_SITE_TESTS)
def test_site_integration(site: type[BaseChatDownloader], test: dict[str, Any]) -> None:
    _run_site_integration(site, test)


class _SuccessfulDownloader:
    def __init__(self) -> None:
        self.closed = False

    def get_chat(self, **params: Any) -> Chat:
        self.params = params
        return Chat(iter(()), id="test", title="Test chat")

    def close(self) -> None:
        self.closed = True


def test_network_runner_requires_expected_exception() -> None:
    downloader = _SuccessfulDownloader()
    scenario = {
        "params": {"url": "https://example.test"},
        "expected_result": {"error": ValueError},
    }

    with pytest.raises(AssertionError, match="Expected scenario to raise"):
        _run_site_integration(
            BaseChatDownloader,
            scenario,
            downloader_factory=lambda: downloader,
        )

    assert downloader.closed


@pytest.mark.parametrize("phase", ["setup", "iteration"])
@pytest.mark.parametrize(
    ("error_type", "expected_outcome"),
    [
        (LoginRequired, nullcontext()),
        (CaptchaChallengeRequired, pytest.raises(CaptchaChallengeRequired)),
    ],
    ids=["asset-login-required", "provider-challenge"],
)
def test_network_runner_distinguishes_provider_challenges(
    phase, error_type, expected_outcome
):
    chat = MagicMock(spec=Chat)
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat
    if phase == "setup":
        downloader.get_chat.side_effect = error_type
    else:
        chat.__iter__.side_effect = lambda: chat
        chat.__next__.side_effect = error_type

    scenario = {
        "params": {"url": "https://www.youtube.com/watch?v=test"},
        "expected_result": {"error": LoginRequired},
    }

    with expected_outcome:
        _run_site_integration(
            YouTubeChatDownloader,
            scenario,
            downloader_factory=lambda: downloader,
        )

    downloader.close.assert_called_once_with()
    if phase == "setup":
        chat.close.assert_not_called()
    else:
        chat.close.assert_called_once_with()


@pytest.mark.parametrize(
    "scenario",
    [
        scenario
        for scenario in YOUTUBE_EXTRACTOR_TESTS
        if scenario["name"].startswith("Sign in to confirm your age.")
    ],
    ids=lambda scenario: scenario["name"],
)
@pytest.mark.parametrize(
    "playability",
    [
        "age-gate",
        "captcha",
        "captcha-without-status",
        "captcha-generic-unplayable",
        "captcha-recovered",
    ],
)
def test_youtube_age_replay_runner_rejects_player_captcha(
    scenario, playability, monkeypatch
):
    fixture = (
        Path(__file__).parent / "fixtures/youtube/errors/player_captcha_synthetic.json"
    )
    player = json.loads(fixture.read_text())
    status = player["playabilityStatus"]
    if playability == "age-gate":
        del status["errorScreen"]
    elif playability == "captcha-without-status":
        del status["status"]
    elif playability in {"captcha-generic-unplayable", "captcha-recovered"}:
        status.update(status="UNPLAYABLE", reason="Video unavailable")

    watch_bootstrap = Mock(return_value=({}, {}, player))
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata._get_initial_info",
        watch_bootstrap,
    )
    _, _, video_id = scenario["params"]["url"].partition("?v=")
    fallback_data = {"contents": {}}
    fallback_player = {
        "playabilityStatus": {
            "status": "LOGIN_REQUIRED",
            "reason": "Sign in to confirm your age",
        },
        "videoDetails": {"videoId": video_id},
    }
    if playability == "captcha-recovered":
        fallback_data["_chat_downloader_continuation_info"] = {
            "Live chat replay": "replay-token"
        }
        fallback_player["playabilityStatus"] = {"status": "OK"}
        fallback_player["videoDetails"]["isLiveContent"] = True
        message = {
            "message_type": "text_message",
            "message_id": "synthetic-message",
            "timestamp": 0,
            "author": {},
            "message": "Replay recovered",
        }
        monkeypatch.setattr(
            YouTubeChatDownloader,
            "_get_chat_messages",
            Mock(return_value=iter([message])),
        )
        scenario = {
            **scenario,
            "expected_result": {
                "messages_condition": lambda result: result == [message]
            },
        }
    mobile_bootstrap = Mock(return_value=(fallback_data, {}, fallback_player))
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.video_metadata.get_innertube_video_bootstrap",
        mobile_bootstrap,
    )
    if playability in {"age-gate", "captcha-recovered"}:
        _run_site_integration(YouTubeChatDownloader, scenario)
    else:
        with pytest.raises(
            CaptchaChallengeRequired, match="CAPTCHA verification"
        ) as exc:
            _run_site_integration(YouTubeChatDownloader, scenario)
        assert isinstance(exc.value, VideoUnavailable)
    watch_bootstrap.assert_called_once()
    if playability in {
        "captcha-without-status",
        "captcha-generic-unplayable",
        "captcha-recovered",
    }:
        mobile_bootstrap.assert_called_once()
    else:
        mobile_bootstrap.assert_not_called()


def test_network_runner_requires_positive_outcome_check() -> None:
    downloader = _SuccessfulDownloader()
    scenario = {
        "params": {"url": "https://example.test"},
        "expected_result": {"description": "no observable assertion"},
    }

    with pytest.raises(AssertionError, match="must declare an outcome check"):
        _run_site_integration(
            BaseChatDownloader,
            scenario,
            downloader_factory=lambda: downloader,
        )

    assert downloader.closed


@pytest.mark.parametrize(
    "scenario",
    [
        BASE_EXTRACTOR_TESTS[0],
        TWITCH_EXTRACTOR_TESTS[0],
        *YOUTUBE_EXTRACTOR_TESTS[:4],
    ],
    ids=lambda scenario: scenario["name"],
)
def test_network_runner_allows_empty_streams_for_metadata_discovery(
    scenario,
):
    downloader = _SuccessfulDownloader()
    _run_site_integration(
        BaseChatDownloader,
        scenario,
        downloader_factory=lambda: downloader,
    )
    assert downloader.closed


@pytest.mark.parametrize(
    ("scenario", "expected_url"),
    [
        (TWITCH_EXTRACTOR_TESTS[1], "https://www.twitch.tv/active-channel"),
        (
            YOUTUBE_EXTRACTOR_TESTS[4],
            "https://www.youtube.com/channel/active-channel",
        ),
    ],
    ids=["twitch", "youtube"],
)
def test_network_runner_rejects_empty_delivery_stream(
    scenario, expected_url, monkeypatch
):
    monkeypatch.setenv(scenario["requires_env"], "active-channel")
    downloader = _SuccessfulDownloader()

    def create_downloader():
        monkeypatch.setenv(scenario["requires_env"], "changed-after-selection")
        return downloader

    with pytest.raises(AssertionError, match="message delivery check failed"):
        _run_site_integration(
            BaseChatDownloader,
            scenario,
            downloader_factory=create_downloader,
        )
    assert downloader.closed
    assert downloader.params["url"] == expected_url


@pytest.mark.parametrize(
    "scenario",
    [TWITCH_EXTRACTOR_TESTS[1], YOUTUBE_EXTRACTOR_TESTS[4]],
    ids=lambda scenario: scenario["name"],
)
def test_network_runner_skips_delivery_without_an_operator_selected_channel(
    scenario, monkeypatch
):
    monkeypatch.delenv(scenario["requires_env"], raising=False)
    with pytest.raises(pytest.skip.Exception, match="active public channel"):
        _run_site_integration(
            BaseChatDownloader,
            scenario,
            downloader_factory=lambda: pytest.fail("Missing channel must skip setup."),
        )

# SPDX-License-Identifier: MIT

"""Offline contracts for the network scenario runner."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any
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
    TwitchChatDownloader,
    YouTubeChatDownloader,
)
from chat_downloader.sites.models import Chat
from tests.conftest import _check_network_marks
from tests.fixtures.extractor_tests import TWITCH_EXTRACTOR_TESTS
from tests.fixtures.youtube.extractor_tests import YOUTUBE_EXTRACTOR_TESTS
from tests.test_chat_downloader import _case_marks, _run_site_integration
from tests.test_youtube_mobile_replay_unit import _replay


@pytest.mark.parametrize("scope", [{}, {"scope": "stable"}, {"scope": []}])
def test_network_scenarios_require_an_explicit_valid_scope(scope) -> None:
    with pytest.raises(ValueError, match="invalid or missing scope"):
        _case_marks({"name": "unclassified replay", **scope})


def test_network_replay_scope_rejects_operator_selected_delivery() -> None:
    scenario = {
        **TWITCH_EXTRACTOR_TESTS[1],
        "scope": "replay",
    }

    with pytest.raises(ValueError, match="requires operator input"):
        _case_marks(scenario)


@pytest.mark.parametrize(
    ("marks", "error"),
    [
        ([], None),
        (
            [pytest.mark.network_live, pytest.mark.timeout(90)],
            "network scope",
        ),
        ([pytest.mark.network], "network scope"),
        (
            [pytest.mark.network, pytest.mark.network_live, pytest.mark.network_replay],
            "network scope",
        ),
        ([pytest.mark.network, pytest.mark.network_replay], "network test timeout"),
        (
            [
                pytest.mark.network,
                pytest.mark.network_environment,
                pytest.mark.timeout(0),
            ],
            "network test timeout",
        ),
        (
            [pytest.mark.network, pytest.mark.network_live, pytest.mark.timeout(90)],
            None,
        ),
    ],
)
def test_collection_requires_one_network_scope_and_a_hard_timeout(marks, error):
    item = Mock(spec=pytest.Item)
    item.nodeid = "test_network.py::test_example"
    collection_marks = {mark.name: mark.mark for mark in marks}
    item.iter_markers.return_value = iter(collection_marks.values())
    item.get_closest_marker.side_effect = collection_marks.get

    outcome = pytest.raises(pytest.UsageError, match=error) if error else nullcontext()
    with outcome:
        _check_network_marks(item)


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

    with pytest.raises(AssertionError, match="got clean EOF"):
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
        (RuntimeError, pytest.raises(RuntimeError)),
    ],
    ids=["asset-login-required", "provider-challenge", "unexpected-error"],
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


def test_network_runner_rejects_delivery_even_if_expected_error_follows() -> None:
    chat = MagicMock(spec=Chat)
    chat.__next__.side_effect = [{"message_type": "text_message"}, LoginRequired]
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat
    scenario = {
        "params": {"url": "https://www.youtube.com/watch?v=test"},
        "expected_result": {"error": LoginRequired},
    }

    with pytest.raises(AssertionError, match="got a first message"):
        _run_site_integration(
            YouTubeChatDownloader, scenario, downloader_factory=lambda: downloader
        )

    chat.__next__.assert_called_once_with()
    chat.close.assert_called_once_with()
    downloader.close.assert_called_once_with()


def test_network_runner_accepts_captured_mobile_replay_text(monkeypatch):
    chat = Chat(_replay(monkeypatch, ["messages"]), status="was_live")
    chat.site = Mock(spec=YouTubeChatDownloader)
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat

    def check_capture(resolved_chat, messages):
        assert resolved_chat is chat
        assert messages[0]["message"] == ":thanksdoc:" * 6
        assert messages[0].get("timestamp") is None
        assert messages[0]["time_in_seconds"] == 8392.57

    scenario = {
        "params": {"url": "https://www.youtube.com/watch?v=fixture"},
        "expected_result": {
            "message_types": ["text_message"],
            "after_capture": check_capture,
        },
    }
    _run_site_integration(
        BaseChatDownloader, scenario, downloader_factory=lambda: downloader
    )
    downloader.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("provider", "status", "timing", "outcome"),
    [
        pytest.param(
            YouTubeChatDownloader,
            "live",
            {"received_timestamp": 123},
            nullcontext(),
            id="youtube-live-receive",
        ),
        pytest.param(
            YouTubeChatDownloader,
            "live",
            {"received_timestamp": True},
            pytest.raises(AssertionError, match="receive time"),
            id="youtube-live-invalid-receive",
        ),
        pytest.param(
            YouTubeChatDownloader,
            "was_live",
            {"time_in_seconds": 0},
            pytest.raises(AssertionError, match="timing provenance"),
            id="youtube-replay-missing-provenance",
        ),
        pytest.param(
            YouTubeChatDownloader,
            "was_live",
            {"received_timestamp": 123},
            pytest.raises(AssertionError, match="provider replay offset"),
            id="youtube-replay-rejects-receive-only",
        ),
        pytest.param(
            TwitchChatDownloader,
            "live",
            {"received_timestamp": 123},
            pytest.raises(AssertionError, match="source timestamp"),
            id="twitch-requires-source-time",
        ),
    ],
)
def test_network_runner_checks_timing_from_resolved_provider(
    provider, status, timing, outcome
):
    message = {
        "message_type": "text_message",
        "message_id": "fixture",
        "author": {},
        "message": "hello",
        **timing,
    }
    chat = Chat(iter([message]), status=status)
    chat.site = Mock(spec=provider)
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat
    scenario = {
        "params": {"url": "https://example.test"},
        "expected_result": {
            "messages_condition": lambda messages: messages == [message]
        },
    }
    with outcome:
        _run_site_integration(
            BaseChatDownloader, scenario, downloader_factory=lambda: downloader
        )
    downloader.close.assert_called_once_with()


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
    ("site", "scenario"),
    [
        pytest.param(TwitchChatDownloader, TWITCH_EXTRACTOR_TESTS[0], id="twitch"),
        *(
            pytest.param(YouTubeChatDownloader, scenario, id=scenario["name"])
            for scenario in YOUTUBE_EXTRACTOR_TESTS[:4]
        ),
    ],
)
def test_network_runner_allows_empty_streams_for_metadata_discovery(
    site,
    scenario,
):
    youtube = site is YouTubeChatDownloader
    chat = Chat(
        id="lofigirl" if youtube else "xenova",
        title="lofigirl" if youtube else "xenova",
        status=None if youtube else "upcoming",
        video_type=None if youtube else "video",
    )
    chat._metadata_pending = youtube
    chat.site = Mock(spec=YouTubeChatDownloader if youtube else TwitchChatDownloader)

    def resolve_metadata():
        if youtube:
            chat.id, chat.title, chat.status = "jfKfPfyJRdk", "Lofi radio", "live"
            chat._metadata_pending = False
        yield from ()

    chat.chat = resolve_metadata()
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat
    _run_site_integration(site, scenario, downloader_factory=lambda: downloader)
    downloader.close.assert_called_once_with()


def test_network_runner_rejects_unresolved_youtube_selector() -> None:
    chat = Chat(iter(()), id="lofigirl", title="lofigirl")
    chat._metadata_pending = True
    chat.site = Mock(spec=YouTubeChatDownloader)
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat
    with pytest.raises(AssertionError, match="metadata check failed"):
        _run_site_integration(
            YouTubeChatDownloader,
            YOUTUBE_EXTRACTOR_TESTS[1],
            downloader_factory=lambda: downloader,
        )
    downloader.close.assert_called_once_with()


_DELIVERY_SCENARIOS = [
    scenario
    for scenario in [*TWITCH_EXTRACTOR_TESTS, *YOUTUBE_EXTRACTOR_TESTS]
    if scenario.get("requires_env")
]


@pytest.mark.parametrize(
    "scenario", _DELIVERY_SCENARIOS, ids=lambda scenario: scenario["requires_env"]
)
def test_network_runner_rejects_empty_delivery_stream(scenario, monkeypatch):
    monkeypatch.setenv(scenario["requires_env"], "active-channel")
    downloader = _SuccessfulDownloader()

    def create_downloader():
        monkeypatch.setenv(scenario["requires_env"], "changed-after-selection")
        return downloader

    with pytest.raises(AssertionError, match="Expected 1 messages, received 0"):
        _run_site_integration(
            BaseChatDownloader,
            scenario,
            downloader_factory=create_downloader,
        )
    assert downloader.closed
    assert downloader.params["url"] == scenario["params"]["url"].format(
        channel="active-channel"
    )
    assert downloader.params["max_messages"] == 1
    assert downloader.params["timeout"] == 30


@pytest.mark.parametrize(
    "scenario", _DELIVERY_SCENARIOS, ids=lambda scenario: scenario["requires_env"]
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


@pytest.mark.parametrize(
    ("message_types", "termination", "failure"),
    [
        (["highlighted_message"], "message_limit", None),
        (["announcement"], "message_limit", None),
        (["room_state"], "message_limit", "Unexpected live message type"),
        (["announcement"], "timeout", "message_limit"),
        (["announcement", "highlighted_message"], "message_limit", "received 2"),
    ],
)
def test_twitch_one_message_delivery_checks_application_records(
    message_types, termination, failure, monkeypatch
):
    scenario = next(
        case
        for case in _DELIVERY_SCENARIOS
        if case["requires_env"] == "TWITCH_TEST_CHANNEL"
    )
    monkeypatch.setenv(scenario["requires_env"], "active-channel")
    chat = Chat(iter({"message_type": kind} for kind in message_types))
    chat.diagnostics["termination_reason"] = termination
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat

    outcome = pytest.raises(AssertionError, match=failure) if failure else nullcontext()
    with outcome:
        _run_site_integration(
            BaseChatDownloader, scenario, downloader_factory=lambda: downloader
        )
    downloader.close.assert_called_once_with()


def test_youtube_live_delivery_rejects_replay(monkeypatch) -> None:
    scenario = next(
        case
        for case in _DELIVERY_SCENARIOS
        if case["requires_env"] == "YOUTUBE_TEST_CHANNEL_ID"
    )
    monkeypatch.setenv(scenario["requires_env"], "active-channel")
    chat = Chat(
        iter(
            [
                {
                    "message_type": "text_message",
                    "message_id": "fixture-message",
                    "timestamp": 123,
                    "author": {},
                    "message": "hello",
                }
            ]
        ),
        status="was_live",
    )
    chat.diagnostics["termination_reason"] = "message_limit"
    downloader = Mock(spec=ChatDownloader)
    downloader.get_chat.return_value = chat

    with pytest.raises(AssertionError, match="Expected live or upcoming YouTube chat"):
        _run_site_integration(
            BaseChatDownloader, scenario, downloader_factory=lambda: downloader
        )


def test_youtube_replay_pacing_override_is_limited_to_all_message_window() -> None:
    overrides = [
        (scenario["params"]["url"], scenario["params"]["youtube_replay_poll_interval"])
        for scenario in YOUTUBE_EXTRACTOR_TESTS
        if "youtube_replay_poll_interval" in scenario["params"]
    ]
    assert overrides == [("https://www.youtube.com/watch?v=97w16cYskVI", 1.0)]

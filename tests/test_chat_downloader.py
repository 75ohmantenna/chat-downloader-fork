# SPDX-License-Identifier: MIT

"""Live provider contract checks and their scenario runner."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import pytest

from chat_downloader import ChatDownloader
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


def _is_live_case(test: dict[str, Any]) -> bool:
    """Return whether a scenario targets mutable live-channel state."""
    url = str(test["params"].get("url", "")).lower()
    if "kick.com/" in url and "/videos/" not in url:
        return True
    if "twitch.tv/" in url and "/videos/" not in url and "clips.twitch.tv/" not in url:
        return True
    return "youtube.com/" in url and not any(
        path in url for path in ("/watch?", "/clip/")
    )


def _case_marks(test: dict[str, Any]) -> tuple[pytest.MarkDecorator, ...]:
    kind = (
        pytest.mark.network_live if _is_live_case(test) else pytest.mark.network_replay
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
def test_network_runner_keeps_discovery_and_inactivity_empty_stream_contracts(
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

# SPDX-License-Identifier: MIT

"""Live provider contract checks and their scenario runner."""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING, Any, cast

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.errors import (
    CaptchaChallengeRequired,
)
from chat_downloader.sites import (
    BaseChatDownloader,
    KickChatDownloader,
    TwitchChatDownloader,
    get_all_sites,
)
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.video_status_models import REPLAY_STATUSES
from tests.fixtures.extractor_tests import (
    BASE_EXTRACTOR_TESTS,
    KICK_EXTRACTOR_TESTS,
    TWITCH_EXTRACTOR_TESTS,
)
from tests.fixtures.youtube.extractor_tests import YOUTUBE_EXTRACTOR_TESTS

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.sites.models import Chat

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


def _check_text_timing(chat: Chat, message: dict[str, Any]) -> None:
    timestamp = message.get("timestamp")
    if timestamp is not None:
        assert type(timestamp) is int, "Invalid source timestamp."
        assert timestamp >= 0, "Invalid source timestamp."
        return

    assert isinstance(chat.site, YouTubeChatDownloader), "Missing source timestamp."
    if chat.status not in REPLAY_STATUSES:
        received = message.get("received_timestamp")
        assert type(received) is int, "Missing valid receive time."
        assert received >= 0, "Invalid receive time."
        return

    offset = message.get("time_in_seconds")
    assert isinstance(offset, (int, float)), "Missing provider replay offset."
    assert not isinstance(offset, bool), "Invalid provider replay offset."
    assert not isinstance(offset, float) or math.isfinite(offset), (
        "Invalid provider replay offset."
    )
    timing = message.get("timing")
    assert isinstance(timing, dict), "Missing replay timing provenance."
    assert timing.get("offset_source") == "provider_replay"
    assert timing.get("absolute_timestamp_available") is False
    assert timing.get("zero_offset_ambiguous") is (offset == 0)


def _check_scenario_outcome(
    chat: Chat, messages: list[dict[str, Any]], expected_result: dict[str, Any]
) -> None:
    for message in messages:
        assert isinstance(message.get("message_type"), str)
        if message["message_type"] == "text_message":
            assert message.get("message_id")
            _check_text_timing(chat, message)
            assert isinstance(message.get("author"), dict)
            assert isinstance(message.get("message"), str)

    checked_outcomes = {
        "chat_condition",
        "messages_condition",
        "message_types",
        "action_types",
        "after_capture",
    } & expected_result.keys()
    assert checked_outcomes, "Successful scenario must declare an outcome check."

    if "chat_condition" in expected_result:
        assert expected_result["chat_condition"](chat), (
            "Scenario metadata check failed."
        )
    if "messages_condition" in expected_result:
        assert expected_result["messages_condition"](messages), (
            "Scenario message delivery check failed."
        )
    for key, field in (
        ("message_types", "message_type"),
        ("action_types", "action_type"),
    ):
        if key in expected_result:
            assert set(expected_result[key]) == {
                message.get(field) for message in messages
            }
    if "after_capture" in expected_result:
        expected_result["after_capture"](chat, messages)


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
                first_message = next(chat, None)
            except CaptchaChallengeRequired:
                raise
            except expected_errors:
                return
            error_names = ", ".join(error.__name__ for error in expected_errors)
            observed = "a first message" if first_message is not None else "clean EOF"
            raise AssertionError(
                f"Expected scenario to raise one of: {error_names}; got {observed}"
            )

        chat = cast("Chat", downloader.get_chat(**params))
        if site is not BaseChatDownloader:
            assert chat.site is not None
            assert chat.site.__class__ is site

        messages = list(chat)
        _check_scenario_outcome(chat, messages, expected_result)
    finally:
        if chat is not None:
            chat.close()
        downloader.close()


@pytest.mark.parametrize(("site", "test"), _ALL_SITE_TESTS)
def test_site_integration(site: type[BaseChatDownloader], test: dict[str, Any]) -> None:
    _run_site_integration(site, test)

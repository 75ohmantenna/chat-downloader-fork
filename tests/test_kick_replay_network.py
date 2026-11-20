# SPDX-License-Identifier: MIT

"""Opt-in replay checks against an explicitly selected, expiring Kick asset."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import pytest

from chat_downloader import ChatDownloader
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.utils.time_utils import ensure_seconds
from tests.kick_helpers import assert_replay_contract

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_environment,
    pytest.mark.timeout(90),
]


def _relative_seconds(value):
    if type(value) not in {int, float, str} or (
        isinstance(value, str) and not value.strip()
    ):
        return None
    return ensure_seconds(value)


def _validate_case(case):
    if not isinstance(case, dict):
        pytest.fail("Kick replay contract must be an object.", pytrace=False)
    url = case.get("url")
    if not isinstance(url, str):
        pytest.fail("Kick replay contract requires a Kick VOD URL.", pytrace=False)
    if url.startswith("//"):
        url = "https:" + url
    elif not urlparse(url).scheme:
        url = "https://" + url
    match = KickChatDownloader.matches(url)
    if not match or match[0] != "_get_chat_by_video":
        pytest.fail("Kick replay contract requires a Kick VOD URL.", pytrace=False)
    if case.get("recording_id") != match[1]["video_id"]:
        pytest.fail(
            "Kick replay contract recording_id must match its VOD URL.", pytrace=False
        )
    start = _relative_seconds(case.get("start_time"))
    end = _relative_seconds(case.get("end_time"))
    if start is None or end is None or not 0 <= start <= end:
        pytest.fail(
            "Kick replay contract requires ordered, finite recording-relative bounds.",
            pytrace=False,
        )
    expected = case.get("expected")
    if not isinstance(expected, dict) or not expected:
        pytest.fail(
            "Kick replay contract requires independently known message IDs.",
            pytrace=False,
        )
    for message_id, fields in expected.items():
        if not message_id.strip():
            pytest.fail(
                "Kick replay contract message IDs must be nonempty strings.",
                pytrace=False,
            )
        if not isinstance(fields, dict) or type(fields.get("timestamp")) is not int:
            pytest.fail(
                "Kick replay contract timestamps must be integer Unix microseconds.",
                pytrace=False,
            )
        offset = fields.get("time_in_seconds")
        if type(offset) not in {int, float} or not start <= offset <= end:
            pytest.fail(
                "Kick replay message times must be inside the relative window.",
                pytrace=False,
            )
    probes = case.get("text_probes")
    if not isinstance(probes, dict) or not probes:
        pytest.fail("Kick replay contract requires text probes.", pytrace=False)
    if not probes.keys() <= expected.keys() or not all(
        isinstance(text, str) for text in probes.values()
    ):
        pytest.fail(
            "Kick replay contract text probes must be strings keyed by expected IDs.",
            pytrace=False,
        )


def test_external_replay_matches_independently_recorded_message_contract():
    case_path = os.environ.get("KICK_TEST_REPLAY_CASE")
    if not case_path:
        pytest.skip("Set KICK_TEST_REPLAY_CASE to a current recorded replay contract.")
    case = json.loads(Path(case_path).read_text(encoding="utf-8"))
    _validate_case(case)
    with closing(ChatDownloader()) as downloader:
        chat = downloader.get_chat(
            case["url"],
            start_time=case["start_time"],
            end_time=case["end_time"],
            message_groups=["messages"],
            max_attempts=2,
            interruptible_retry=False,
        )
        with closing(chat):
            messages = list(chat)
            assert chat.id == case["recording_id"]
            assert chat.status == "completed"
            assert_replay_contract(messages, case["expected"])
            by_id = {message["message_id"]: message for message in messages}
            for message_id, text in case["text_probes"].items():
                assert by_id[message_id]["message"] == text
            assert chat.diagnostics["history_complete"] is True
            assert chat.diagnostics["termination_reason"] == "completed"
            for reason in ("parse_error", "malformed_timestamp", "malformed_object"):
                assert chat.diagnostics.get(reason, 0) == 0

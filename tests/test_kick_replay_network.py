# SPDX-License-Identifier: MIT

"""Opt-in replay checks against an explicitly selected, expiring Kick asset."""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path

import pytest

from chat_downloader import ChatDownloader
from tests.kick_helpers import assert_replay_contract

pytestmark = [
    pytest.mark.network,
    pytest.mark.network_environment,
    pytest.mark.timeout(90),
]


def test_external_replay_matches_independently_recorded_message_contract():
    case_path = os.environ.get("KICK_TEST_REPLAY_CASE")
    if not case_path:
        pytest.skip("Set KICK_TEST_REPLAY_CASE to a current recorded replay contract.")
    case = json.loads(Path(case_path).read_text(encoding="utf-8"))
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

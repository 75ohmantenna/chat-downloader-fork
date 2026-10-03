# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import sqlite3
from typing import ClassVar
from unittest.mock import Mock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.kick.api_client import PreloadedChatState
from chat_downloader.sites.kick.constants import CHAT_MESSAGE_EVENT, SUBSCRIPTION_EVENT
from chat_downloader.sites.kick.live_service import get_chat_by_channel
from tests.kick_helpers import (
    FakeTransport,
    fixture_frame,
    load_fixture,
)


class LiveDownloader:
    _NAME = "kick.com"
    _http_timeout = (10, 30)
    frames = None
    status = "live"
    failure = None
    inspection_failure = None

    def __init__(self, **kwargs):
        channel = load_fixture(f"channel_{self.status}.json")
        self._kick_client = Mock()
        self._kick_client.fetch_channel.return_value = channel
        self._kick_client.fetch_preloaded_chat_state.return_value = PreloadedChatState(
            [], None
        )
        self._kick_client.close = Mock()

    def get_chat(self, **kwargs):
        request = ChatRequest.from_kwargs(**kwargs)
        frames = self.frames if self.frames is not None else [_text_frame()]
        if self.failure:
            frames = [*frames, self.failure]

        def frame_iterator(_transport):
            for frame in frames:
                if isinstance(frame, BaseException):
                    raise frame
                yield frame

        chat = get_chat_by_channel(
            self,
            "examplechannel",
            request,
            transport_factory=FakeTransport,
            frame_iterator=frame_iterator,
        )
        configure_chat(chat, request, self)
        if self.inspection_failure:
            chat._capture_inspector = Mock(side_effect=self.inspection_failure)
        return chat

    def is_live_status(self, status):
        return status in {"live", "idle"}

    def resolve_live_format(self, name):
        return name

    def close(self):
        self._kick_client.close()


def _text_frame():
    return fixture_frame(CHAT_MESSAGE_EVENT, "chat_message_event_data.json")


def _params(tmp_path, **changes):
    return {
        "url": "https://kick.com/examplechannel",
        "output": [str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        "format": "kick",
        "message_groups": ["all"],
        "quiet": True,
        "verify_output": True,
        "run_manifest": str(tmp_path / "run.json"),
        **changes,
    }


@pytest.mark.parametrize("status", ["live", "offline"])
def test_live_service_composes_inspection_parity_manifest_and_debug_summary(
    tmp_path, status, caplog
):
    class Live(LiveDownloader):
        pass

    Live.status = status
    caplog.set_level("DEBUG", logger="chat_downloader")
    result = execute_run(Live, **_params(tmp_path))
    assert result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "ok"
    assert result.provider_inspection["records"] == 1
    assert result.provider_inspection["frame_accounting"]["emitted_minus_records"] == 0
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection
    assert manifest["recording"]["site_name"] == "kick.com"
    assert "'provider_inspection': {'jsonl_lines': 1" in caplog.text


def test_filters_and_duplicate_suppression_do_not_create_inspection_gaps(tmp_path):
    class Filtered(LiveDownloader):
        frames: ClassVar[list] = [
            _text_frame(),
            _text_frame(),
            fixture_frame(SUBSCRIPTION_EVENT, "subscription_event_compact.json"),
        ]

    result = execute_run(Filtered, **_params(tmp_path, message_groups=["messages"]))
    assert result.success
    assert result.message_count == 1
    accounting = result.provider_inspection["frame_accounting"]
    assert accounting["parsed_event_count"] == 3
    assert accounting["live_emitted_count"] == 1
    assert accounting["emitted_minus_records"] == 0


def test_accounted_parser_loss_fails_run_while_parity_passes(tmp_path):
    class Malformed(LiveDownloader):
        frames: ClassVar[list] = [
            _text_frame(),
            {"event": CHAT_MESSAGE_EVENT, "data": "PRIVATE_SENTINEL"},
        ]

    result = execute_run(Malformed, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "review"
    accounting = result.provider_inspection["frame_accounting"]
    assert accounting["unaccounted_frames"] == 0
    assert accounting["malformed_event_count"] == 1
    assert "PRIVATE_SENTINEL" not in json.dumps(result.provider_inspection)


def test_no_verification_leaves_provider_inspection_unrequested(tmp_path):
    result = execute_run(LiveDownloader, **_params(tmp_path, verify_output=False))
    assert result.success
    assert result.parity_status == "not_requested"
    assert result.provider_inspection is None


def test_empty_live_inspection_preserves_lazy_outputs(tmp_path):
    class Empty(LiveDownloader):
        frames: ClassVar[list] = []

    result = execute_run(Empty, **_params(tmp_path))
    assert result.success
    assert result.provider_inspection["records"] == 0
    assert result.provider_inspection["frame_accounting"]["summary_minus_records"] == 0
    assert not (tmp_path / "chat.jsonl").exists()
    assert not (tmp_path / "chat.txt").exists()


@pytest.mark.parametrize("error", [ValueError("primary"), KeyboardInterrupt()])
def test_closed_partial_capture_inspection_preserves_primary_failure(tmp_path, error):
    class Failed(LiveDownloader):
        failure = error

    result = execute_run(Failed, **_params(tmp_path))
    assert not result.success
    assert result.error_message == (
        "Keyboard Interrupt" if isinstance(error, KeyboardInterrupt) else "primary"
    )
    assert result.parity_status == "not_run"
    assert result.provider_inspection["records"] == 1
    assert result.provider_inspection["frame_accounting"]["run_failed"] == 1


@pytest.mark.parametrize(
    "error", [OSError("PRIVATE"), sqlite3.OperationalError("PRIVATE")]
)
def test_inspector_errors_are_private_and_do_not_skip_parity(tmp_path, error):
    class Failed(LiveDownloader):
        inspection_failure = error

    result = execute_run(Failed, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection == {
        "status": "error",
        "error": "provider_inspection_failed",
    }


def test_inspection_failure_does_not_mask_retrieval_failure(tmp_path):
    class Failed(LiveDownloader):
        failure = ValueError("primary")
        inspection_failure = OSError("PRIVATE")

    result = execute_run(Failed, **_params(tmp_path))
    assert result.error_message == "primary"
    assert result.provider_inspection["status"] == "error"
    assert result.parity_status == "not_run"


def test_provider_findings_survive_parity_failure(tmp_path):
    class BrokenText(LiveDownloader):
        def close(self):
            super().close()
            (tmp_path / "chat.txt").write_text("wrong\n")

    result = execute_run(BrokenText, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "failed"
    assert result.provider_inspection["status"] == "ok"


@pytest.mark.parametrize("complete", [True, False])
def test_deadline_prefetch_is_subtracted_and_incomplete_accounting_requires_review(
    tmp_path, complete
):
    class Deadline(LiveDownloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            source = chat.chat

            class Source:
                def __iter__(self):
                    return self

                def __next__(self):
                    return next(source)

                def close(self):
                    source.close()
                    chat.diagnostics["live_emitted_count"] += 1

                def deadline_prefetch_summary(self):
                    return 1, complete

            chat.chat = Source()
            return chat

    result = execute_run(Deadline, **_params(tmp_path))
    assert result.success is complete
    report = result.provider_inspection
    assert report["frame_accounting"]["emitted_minus_records"] == 0
    assert report["prefetched_after_deadline_count"] == 1
    assert report["deadline_prefetch_count_complete"] is complete


def test_invalid_live_counter_is_an_inspection_error(tmp_path):
    class Invalid(LiveDownloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.diagnostics["public_subscription_count"] = True
            return chat

    result = execute_run(Invalid, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "error"


@pytest.mark.parametrize(
    "key",
    ["reconnect_backfill_truncated_count", "reconnect_backfill_truncated_microseconds"],
)
def test_known_reconnect_history_gap_requires_review_even_when_parity_passes(
    tmp_path, key
):
    class Gap(LiveDownloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.diagnostics[key] = 1
            return chat

    result = execute_run(Gap, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "review"
    assert result.provider_inspection["frame_accounting"][key] == 1

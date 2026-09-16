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
from chat_downloader.sites.twitch.live_service import get_chat_by_stream_id
from tests.test_twitch_capture_inspection_unit import _text


class LiveDownloader:
    _NAME = "Twitch.tv"
    rows = None
    stream_type = "live"
    failure = None
    inspection_failure = None

    def __init__(self, **kwargs):
        self._download_gql = Mock(
            return_value=[
                {
                    "data": {
                        "user": {
                            "id": "channel",
                            "stream": {"type": self.stream_type},
                            "lastBroadcast": {"title": "test"},
                        }
                    }
                }
            ]
        )
        self._update_badge_info = Mock()

    def _get_chat_messages_by_stream_id(self, stream_id, request, *, diagnostics):
        rows = self.rows if self.rows is not None else [_text()]
        for row in rows:
            diagnostics.increment("received_irc_frame_count")
            diagnostics.increment("parsed_irc_message_count")
            yield row
        if self.failure:
            raise self.failure

    def get_chat(self, **kwargs):
        request = ChatRequest.from_kwargs(**kwargs)
        chat = get_chat_by_stream_id(self, "test", request)
        configure_chat(chat, request, self)
        if self.inspection_failure:
            chat._capture_inspector = Mock(side_effect=self.inspection_failure)
        return chat

    def is_live_status(self, status):
        return status in {"live", "upcoming"}

    def resolve_live_format(self, name):
        return name

    def close(self):
        pass


def _params(tmp_path):
    return {
        "output": [str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        "format": "twitch",
        "quiet": True,
        "verify_output": True,
        "run_manifest": str(tmp_path / "run.json"),
    }


@pytest.mark.parametrize("stream_type", ["live", None])
def test_live_service_composes_inspection_parity_and_manifest(tmp_path, stream_type):
    class Live(LiveDownloader):
        pass

    Live.stream_type = stream_type
    result = execute_run(Live, **_params(tmp_path))
    assert result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "ok"
    assert result.provider_inspection["records"] == 1
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["provider_inspection"] == result.provider_inspection
    assert manifest["recording"]["site_name"] == "Twitch.tv"


def test_review_preserves_successful_parity(tmp_path):
    class Unknown(LiveDownloader):
        rows: ClassVar[list] = [{"message_type": "PRIVATE_SENTINEL"}]

    result = execute_run(Unknown, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection["status"] == "review"
    assert "PRIVATE_SENTINEL" not in json.dumps(result.provider_inspection)


@pytest.mark.parametrize(
    "error", [OSError("PRIVATE"), sqlite3.OperationalError("PRIVATE")]
)
def test_inspector_errors_are_sanitized_without_skipping_parity(tmp_path, error):
    class Failed(LiveDownloader):
        inspection_failure = error

    result = execute_run(Failed, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "passed"
    assert result.provider_inspection == {
        "status": "error",
        "error": "provider_inspection_failed",
    }


def test_empty_live_inspection_preserves_lazy_outputs(tmp_path):
    class Empty(LiveDownloader):
        rows: ClassVar[list] = []

    result = execute_run(Empty, **_params(tmp_path))
    assert result.success
    assert result.provider_inspection["records"] == 0
    assert not (tmp_path / "chat.jsonl").exists()
    assert not (tmp_path / "chat.txt").exists()


def test_primary_retrieval_error_preserves_inspection_report(tmp_path):
    class Failed(LiveDownloader):
        failure = ValueError("primary")

    result = execute_run(Failed, **_params(tmp_path))
    assert not result.success
    assert result.error_message == "primary"
    assert result.provider_inspection["status"] == "ok"
    assert result.parity_status == "not_run"


def test_provider_report_survives_parity_failure(tmp_path):
    class BrokenText(LiveDownloader):
        def close(self):
            (tmp_path / "chat.txt").write_text("wrong\n")

    result = execute_run(BrokenText, **_params(tmp_path))
    assert not result.success
    assert result.parity_status == "failed"
    assert result.provider_inspection["status"] == "ok"


def test_deadline_prefetch_information_is_retained(tmp_path):
    class Deadline(LiveDownloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            source = chat.chat

            class Source:
                def __iter__(self):
                    return self

                def __next__(self):
                    return next(source)

                def deadline_prefetch_summary(self):
                    return 1, False

            chat.chat = Source()
            return chat

    result = execute_run(Deadline, **_params(tmp_path))
    assert result.provider_inspection["prefetched_after_deadline_count"] == 1
    assert result.provider_inspection["deadline_prefetch_count_complete"] is False


def test_inspection_error_does_not_mask_partial_capture_failure(tmp_path):
    class Failed(LiveDownloader):
        failure = ValueError("primary")
        inspection_failure = OSError("PRIVATE")

    result = execute_run(Failed, **_params(tmp_path))
    assert result.error_message == "primary"
    assert result.provider_inspection["status"] == "error"
    assert result.parity_status == "not_run"


def test_invalid_provider_counter_becomes_inspection_error(tmp_path):
    class Invalid(LiveDownloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.diagnostics["benign_irc_control_frame_count"] = True
            return chat

    result = execute_run(Invalid, **_params(tmp_path))
    assert result.provider_inspection["status"] == "error"
    assert result.parity_status == "passed"
    assert not result.success


def test_replaced_output_identity_does_not_mask_primary_failure(tmp_path):
    class Replaced(LiveDownloader):
        failure = ValueError("primary")

        def close(self):
            txt = tmp_path / "chat.txt"
            txt.unlink()
            txt.hardlink_to(tmp_path / "chat.jsonl")

    result = execute_run(Replaced, **_params(tmp_path))
    assert result.error_message == "primary"
    assert result.provider_inspection["status"] == "error"
    assert result.parity_status == "not_run"

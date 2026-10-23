"""Compose request diagnostics, manifest windows and verified output aliases."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from chat_downloader import ChatDownloader
from chat_downloader.errors import SiteNotSupported
from chat_downloader.runtime.capture_manifest import RunManifest
from chat_downloader.runtime.runner import RunResult, execute_run
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.test_capture_checkpoint_unit import Downloader, parameters


@pytest.mark.parametrize("attempts", [1, 2])
def test_bootstrap_failure_manifest_keeps_actual_connection_attempts(
    tmp_path, monkeypatch, attempts
):
    get = Mock(side_effect=RequestsConnectionError("PRIVATE credential details"))
    monkeypatch.setattr(YouTubeChatDownloader, "_session_get", get)
    path = tmp_path / "failure.json"
    result = execute_run(
        ChatDownloader,
        url="https://www.youtube.com/watch?v=sxLJv_jA6sE",
        max_attempts=attempts,
        retry_timeout=0,
        run_manifest=str(path),
    )
    report = json.loads(path.read_text())
    assert not result.success
    assert get.call_count == attempts
    assert report["failure_stage"] == "bootstrap"
    counters = report["provider_diagnostics"]
    assert counters["bootstrap_request_count"] == attempts
    assert counters["bootstrap_network_error_count"] == attempts
    assert counters["bootstrap_http_error_count"] == 0
    assert counters["initial_request_profile"] == "youtube_web"
    assert "PRIVATE" not in path.read_text()


def test_reused_provider_resets_failed_request_counters(monkeypatch):
    monkeypatch.setattr(
        YouTubeChatDownloader,
        "_session_get",
        Mock(side_effect=RequestsConnectionError("injected")),
    )
    downloader = ChatDownloader()
    try:
        for attempts in (2, 1):
            with pytest.raises(RequestsConnectionError):
                downloader.get_chat(
                    "https://www.youtube.com/watch?v=sxLJv_jA6sE",
                    max_attempts=attempts,
                    retry_timeout=0,
                )
            assert (
                downloader._request_diagnostics["bootstrap_request_count"] == attempts
            )
        assert len(downloader.sessions) == 1
        with pytest.raises(SiteNotSupported):
            downloader.get_chat("https://example.invalid/")
        assert downloader._request_diagnostics == {}
    finally:
        downloader.close()


def test_failure_diagnostics_include_incomplete_rebootstrap_without_double_counting():
    chat = Chat(diagnostics={"bootstrap_request_count": 2, "poll_count": 1})
    downloader = SimpleNamespace(
        _request_diagnostics={"bootstrap_request_count": 5, "private": "PRIVATE"}
    )
    result = RunResult()
    result.observe_provider(chat, downloader)
    assert result.provider_diagnostics["bootstrap_request_count"] == 5
    assert result.provider_diagnostics["poll_count"] == 1
    downloader._request_diagnostics["bootstrap_request_count"] = 100
    assert result.provider_diagnostics["bootstrap_request_count"] == 5


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, {"start_time": None, "end_time": None}),
        ("00:10:00", "00:10:30", {"start_time": 600, "end_time": 630}),
        (-20, 0, {"start_time": -20, "end_time": 0}),
        (600.25, 601.75, {"start_time": 600.25, "end_time": 601.75}),
        (True, "nan", {"start_time": None, "end_time": None}),
    ],
)
def test_manifest_snapshots_requested_bounds_before_resume_mutation(
    tmp_path, start, end, expected
):
    parameters = {"start_time": start, "end_time": end}
    path = tmp_path / "window.json"
    manifest = RunManifest(str(path), None, parameters=parameters)
    parameters["start_time"] = 599
    manifest.write(Chat(), RunResult())
    report = json.loads(path.read_text())
    assert report["selected_window"] == expected
    assert report["effective_window"]["start_time"] == 599


def test_resumed_manifest_distinguishes_selected_bounds_and_overlap(tmp_path):
    params = parameters(tmp_path)
    params.update(start_time=5, end_time=20)
    first = execute_run(Downloader, max_messages=2, **params)
    assert first.success
    path = tmp_path / "resumed.json"
    result = execute_run(Downloader, run_manifest=str(path), **params)
    assert result.success
    report = json.loads(path.read_text())
    assert report["selected_window"] == {"start_time": 5, "end_time": 20}
    assert report["effective_window"] == {
        "start_time": 9,
        "end_time": 20,
        "time_basis": "recording",
    }


def test_manifest_never_serializes_arbitrary_effective_window_fields(tmp_path):
    chat = Chat()
    chat._request_window = {
        "start_time": "00:10:00",
        "end_time": float("nan"),
        "time_basis": "clip",
        "private_message": "PRIVATE CHAT CONTENT",
    }
    path = tmp_path / "window.json"
    RunManifest(str(path), None).write(chat, RunResult())
    report = json.loads(path.read_text())
    assert report["effective_window"] == {
        "start_time": 600,
        "end_time": None,
        "time_basis": "clip",
    }
    assert "PRIVATE" not in path.read_text()


def test_failed_setup_has_no_effective_window(tmp_path):
    path = tmp_path / "failure.json"
    RunManifest(str(path), None).write(None, RunResult(failure_stage="bootstrap"))
    assert json.loads(path.read_text())["effective_window"] is None


def test_provider_snapshot_accepts_adapters_without_a_setup_ledger():
    result = RunResult()
    result.observe_provider(None, SimpleNamespace(_request_diagnostics=object()))
    assert result.provider_diagnostics == {}


def test_finalized_provider_counters_reach_the_manifest(tmp_path):
    class FinalizingDownloader(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            close = chat.close

            def finalize():
                close()
                chat.diagnostics["poll_count"] = 2

            chat.close = finalize
            return chat

    path = tmp_path / "finalized.json"
    result = execute_run(FinalizingDownloader, run_manifest=str(path), quiet=True)
    assert result.success
    assert result.provider_diagnostics["poll_count"] == 2
    assert json.loads(path.read_text())["provider_diagnostics"]["poll_count"] == 2

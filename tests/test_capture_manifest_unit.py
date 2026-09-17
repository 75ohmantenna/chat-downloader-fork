# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os

import pytest

from chat_downloader.runtime.runner import execute_run
from tests.test_capture_checkpoint_unit import Downloader, message, parameters


@pytest.fixture
def chat_state(monkeypatch):
    original = Downloader.get_chat
    diagnostics = {}

    def get_chat(self, **kwargs):
        chat = original(self, **kwargs)
        chat.diagnostics.update(diagnostics)
        chat.site._NAME = "Twitch.tv"
        return chat

    monkeypatch.setattr(Downloader, "get_chat", get_chat)
    return diagnostics


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "run.json"

    def run(downloader=Downloader, **kwargs):
        result = execute_run(downloader, run_manifest=str(path), **kwargs)
        return result, json.loads(path.read_text())

    return run


@pytest.mark.parametrize("resumed", [False, True], ids=["fresh", "exhausted-resume"])
def test_manifest_certifies_closed_files_and_completed_run(
    tmp_path, resumed, manifest, chat_state
):
    params = parameters(tmp_path)
    if resumed:
        assert execute_run(Downloader, **params).success
    result, report = manifest(**params, require_complete=True)
    assert result.success
    assert report["replay_complete"]
    assert report["parity_status"] == "passed"
    assert report["message_count"] == (0 if resumed else 4)
    assert report["prior_message_count"] == (4 if resumed else 0)
    assert report["recording"]["id"] == "video"
    assert report["recording"]["site_name"] == "Twitch.tv"
    for item in report["outputs"]:
        artifact = tmp_path / item["file_name"].split("/")[-1]
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert "Record 1" not in (tmp_path / "run.json").read_text()


@pytest.mark.parametrize("checkpoint", [False, True], ids=["plain", "checkpointed"])
def test_require_complete_rejects_limit_but_preserves_resume(tmp_path, checkpoint):
    params = parameters(tmp_path)
    if not checkpoint:
        params.pop("resume")
    count = 2 if checkpoint else 4
    result = execute_run(
        Downloader, **params, max_messages=count, require_complete=True
    )
    assert not result.success
    assert result.parity_status == "passed"
    assert result.termination_reason == "message_limit"
    if checkpoint:
        assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == count
        assert execute_run(Downloader, **params, require_complete=True).success


@pytest.mark.parametrize(
    "state",
    [
        {"termination_reason": "timeout"},
        {"termination_reason": "inactivity_timeout"},
        {"history_complete": False},
        {"parse_error": 1},
        {"malformed_timestamp": 1},
        {"malformed_object": 1},
    ],
)
def test_completeness_does_not_certify_partial_or_lossy_replay(
    tmp_path, chat_state, state
):
    chat_state.update(state)
    result = execute_run(Downloader, **parameters(tmp_path), require_complete=True)
    assert not result.success
    assert result.parity_status == "passed"


def test_empty_manifest_does_not_hash_stale_outputs(tmp_path, monkeypatch, manifest):
    monkeypatch.setattr(Downloader, "records", [])
    old = tmp_path / "chat.jsonl"
    old.write_text("old capture")
    result, report = manifest(output=str(old), format="kick", quiet=True)
    assert result.success
    assert report["outputs"][0]["sha256"] is None
    assert old.read_text() == "old capture"


@pytest.mark.parametrize(
    "target",
    [
        "chat.jsonl",
        "checkpoint.json",
        "checkpoint.json.lock",
        "video.jsonl",
    ],
)
def test_manifest_cannot_alias_outputs_or_checkpoint(tmp_path, target):
    params = parameters(tmp_path)
    if target == "video.jsonl":
        params.pop("resume")
        params.pop("verify_output")
        params["output"] = str(tmp_path / "{id}.jsonl")
    assert not execute_run(
        Downloader, **params, run_manifest=str(tmp_path / target)
    ).success
    if target == "video.jsonl":
        assert not (tmp_path / target).exists()
    assert not (tmp_path / "chat.jsonl").exists()


@pytest.mark.parametrize("timing", ["existing", "dangling-link", "close-race"])
def test_manifest_creation_never_clobbers_files(tmp_path, monkeypatch, timing):
    path = tmp_path / "run.json"
    absent = tmp_path / "absent"
    if timing == "dangling-link":
        path.symlink_to(absent)
    elif timing == "existing":
        path.write_text("preserve")
    else:
        monkeypatch.setattr(
            Downloader, "close", lambda self: path.write_text("preserve")
        )
    assert not execute_run(Downloader, quiet=True, run_manifest=str(path)).success
    if timing == "dangling-link":
        assert not absent.exists()
    else:
        assert path.read_text() == "preserve"


def test_failed_metadata_still_has_failure_manifest(monkeypatch, manifest):
    def fail(self, **kwargs):
        raise ValueError("metadata failed")

    monkeypatch.setattr(Downloader, "get_chat", fail)
    result, report = manifest()
    assert not result.success
    assert not report["success"]
    assert not report["replay_complete"]
    assert report["outputs"] == []


def test_interrupted_manifest_reports_failure_and_written_prefix(
    tmp_path, monkeypatch, manifest
):
    monkeypatch.setattr(Downloader, "records", [message(1, 10)])
    monkeypatch.setattr(Downloader, "failure", KeyboardInterrupt())
    result, report = manifest(**parameters(tmp_path))
    assert not result.success
    assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == 1
    monkeypatch.undo()
    assert execute_run(Downloader, **parameters(tmp_path)).message_count == 3
    assert result.interrupted
    assert report["termination_reason"] == "interrupted"
    assert not report["replay_complete"]
    assert report["outputs"][0]["records_written"] == 1


def test_known_loss_survives_resume_without_reappearing_in_later_pages(
    tmp_path, chat_state
):
    chat_state["parse_error"] = 1
    params = parameters(tmp_path)
    first = execute_run(Downloader, **params, max_messages=2)
    assert first.success
    state = json.loads((tmp_path / "checkpoint.json").read_text())
    assert state["record_loss"]
    assert not state["completed"]
    chat_state.clear()
    second = execute_run(Downloader, **params, require_complete=True)
    assert not second.success
    assert second.parity_status == "passed"
    assert json.loads((tmp_path / "checkpoint.json").read_text())["record_loss"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file contract")
def test_manifest_hash_rejects_replaced_fifo_without_blocking(tmp_path):
    output = tmp_path / "chat.jsonl"

    class Replaced(Downloader):
        def close(self):
            output.unlink()
            os.mkfifo(output)

    manifest = tmp_path / "run.json"
    result = execute_run(
        Replaced,
        output=str(output),
        run_manifest=str(manifest),
        format="kick",
        quiet=True,
    )
    assert not result.success
    assert "regular files" in result.error_message
    assert not manifest.exists()

# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
from typing import ClassVar

import pytest

from chat_downloader.runtime.runner import execute_run
from tests.test_capture_checkpoint_unit import Downloader, message, parameters


def test_manifest_certifies_closed_files_and_completed_run(tmp_path):
    params = parameters(tmp_path)
    path = tmp_path / "run.json"
    result = execute_run(
        Downloader, **params, run_manifest=str(path), require_complete=True
    )
    assert result.success
    report = json.loads(path.read_text())
    assert report["replay_complete"]
    assert report["parity_status"] == "passed"
    assert report["message_count"] == 4
    assert report["recording"]["id"] == "video"
    for item in report["outputs"]:
        artifact = tmp_path / item["file_name"].split("/")[-1]
        assert item["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert "Record 1" not in path.read_text()


def test_require_complete_rejects_limit_but_keeps_verified_checkpoint(tmp_path):
    params = parameters(tmp_path)
    result = execute_run(Downloader, **params, max_messages=2, require_complete=True)
    assert not result.success
    assert result.parity_status == "passed"
    assert result.termination_reason == "message_limit"
    assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == 2
    assert execute_run(Downloader, **params, require_complete=True).success


def test_limit_without_checkpoint_is_not_complete(tmp_path):
    params = parameters(tmp_path)
    params.pop("resume")
    result = execute_run(Downloader, **params, max_messages=4, require_complete=True)
    assert not result.success
    assert result.termination_reason == "message_limit"


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
def test_completeness_does_not_certify_partial_or_lossy_replay(tmp_path, state):
    class Partial(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.diagnostics.update(state)
            return chat

    result = execute_run(Partial, **parameters(tmp_path), require_complete=True)
    assert not result.success
    assert result.parity_status == "passed"


def test_complete_rejects_live_before_output(tmp_path):
    class Live(Downloader):
        status = "live"

    params = parameters(tmp_path)
    params.pop("resume")
    assert not execute_run(Live, **params, require_complete=True).success
    assert not (tmp_path / "chat.jsonl").exists()


def test_empty_manifest_does_not_hash_stale_outputs(tmp_path):
    class Empty(Downloader):
        records: ClassVar[list] = []

    old = tmp_path / "chat.jsonl"
    old.write_text("old capture")
    path = tmp_path / "run.json"
    result = execute_run(
        Empty, output=str(old), format="kick", quiet=True, run_manifest=str(path)
    )
    assert result.success
    assert json.loads(path.read_text())["outputs"][0]["sha256"] is None
    assert old.read_text() == "old capture"


@pytest.mark.parametrize(
    "target", ["chat.jsonl", "checkpoint.json", "checkpoint.json.lock"]
)
def test_manifest_cannot_alias_outputs_or_checkpoint(tmp_path, target):
    params = parameters(tmp_path)
    result = execute_run(Downloader, **params, run_manifest=str(tmp_path / target))
    assert not result.success
    assert not (tmp_path / "chat.jsonl").exists()


def test_manifest_rejects_existing_file_and_dangling_symlink(tmp_path):
    path = tmp_path / "run.json"
    path.write_text("preserve")
    assert not execute_run(Downloader, run_manifest=str(path)).success
    assert path.read_text() == "preserve"
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")
    assert not execute_run(Downloader, run_manifest=str(link)).success
    assert not (tmp_path / "absent").exists()


def test_manifest_creation_race_never_clobbers_file(tmp_path):
    path = tmp_path / "run.json"

    class Racing(Downloader):
        def close(self):
            path.write_text("concurrent file")

    assert not execute_run(Racing, quiet=True, run_manifest=str(path)).success
    assert path.read_text() == "concurrent file"


def test_failed_metadata_still_has_failure_manifest(tmp_path):
    class Failed(Downloader):
        def get_chat(self, **kwargs):
            raise ValueError("metadata failed")

    path = tmp_path / "run.json"
    result = execute_run(Failed, run_manifest=str(path))
    assert not result.success
    report = json.loads(path.read_text())
    assert not report["success"]
    assert not report["replay_complete"]
    assert report["outputs"] == []


def test_interrupted_manifest_reports_failure_and_written_prefix(tmp_path):
    class Interrupted(Downloader):
        records: ClassVar[list] = [message(1, 10)]
        failure = KeyboardInterrupt()

    path = tmp_path / "run.json"
    result = execute_run(Interrupted, **parameters(tmp_path), run_manifest=str(path))
    assert result.interrupted
    report = json.loads(path.read_text())
    assert report["termination_reason"] == "interrupted"
    assert not report["replay_complete"]
    assert report["outputs"][0]["records_written"] == 1


def test_known_loss_survives_resume_without_reappearing_in_later_pages(tmp_path):
    class Lossy(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.diagnostics["parse_error"] = 1
            return chat

    params = parameters(tmp_path)
    first = execute_run(Lossy, **params, max_messages=2)
    assert first.success
    state = json.loads((tmp_path / "checkpoint.json").read_text())
    assert state["record_loss"]
    assert not state["completed"]
    second = execute_run(Downloader, **params, require_complete=True)
    assert not second.success
    assert second.parity_status == "passed"
    assert json.loads((tmp_path / "checkpoint.json").read_text())["record_loss"]


def test_zero_new_records_manifest_hashes_checkpoint_verified_existing_files(tmp_path):
    params = parameters(tmp_path)
    assert execute_run(Downloader, **params).success
    path = tmp_path / "resumed.json"
    result = execute_run(
        Downloader, **params, run_manifest=str(path), require_complete=True
    )
    assert result.success
    report = json.loads(path.read_text())
    assert report["message_count"] == 0
    assert report["prior_message_count"] == 4
    assert all(item["sha256"] for item in report["outputs"])


def test_corrupt_checkpoint_loss_flag_is_rejected(tmp_path):
    params = parameters(tmp_path)
    assert execute_run(Downloader, **params).success
    path = tmp_path / "checkpoint.json"
    value = json.loads(path.read_text())
    value["record_loss"] = "false"
    path.write_text(json.dumps(value))
    result = execute_run(Downloader, **params, require_complete=True)
    assert not result.success


def test_expanded_manifest_collision_fails_before_output(tmp_path):
    target = tmp_path / "video.jsonl"
    result = execute_run(
        Downloader,
        output=str(tmp_path / "{id}.jsonl"),
        run_manifest=str(target),
        quiet=True,
        format="kick",
    )
    assert not result.success
    assert not target.exists()


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


def test_manifest_uses_configured_provider_name(tmp_path):
    class Named(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            chat.site._NAME = "Twitch.tv"
            return chat

    path = tmp_path / "manifest.json"
    assert execute_run(Named, quiet=True, run_manifest=str(path)).success
    assert json.loads(path.read_text())["recording"]["site_name"] == "Twitch.tv"

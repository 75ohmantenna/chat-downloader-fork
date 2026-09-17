# SPDX-License-Identifier: MIT

"""Real writer/formatter tests for checkpointed replay and automatic parity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.capture_checkpoint import CaptureCheckpoint, _atomic_json
from chat_downloader.runtime.capture_verification import verify_capture
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.models import Chat


def message(number, offset):
    return {
        "message_id": str(number),
        "message_type": "text_message",
        "message": f"Record {number}",
        "time_in_seconds": offset,
        "time_text": str(offset),
        "timestamp": 1_000_000 + offset * 1_000_000,
    }


class Downloader:
    records: ClassVar[list] = [
        message(1, 10),
        message(2, 10),
        message(3, 10),
        message(4, 11),
    ]
    failure = None
    status = "completed"

    def __init__(self, **kwargs):
        pass

    def get_chat(self, **kwargs):
        request = ChatRequest.from_kwargs(**kwargs)

        def source():
            for item in self.records:
                if item.get("time_in_seconds", 0) >= float(request.start_time or 0):
                    yield item.copy()
            if self.failure is not None:
                raise self.failure

        chat = Chat(source(), id="video", status=self.status)
        configure_chat(chat, request, SimpleNamespace(is_live_status=lambda _: False))
        return chat

    def close(self):
        pass


def parameters(tmp_path):
    return {
        "url": "https://example.invalid/video",
        "output": [str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")],
        "resume": str(tmp_path / "checkpoint.json"),
        "format": "kick",
        "quiet": True,
        "verify_output": True,
    }


def test_resume_preserves_same_timestamp_messages_and_verifies_both_runs(
    tmp_path,
) -> None:
    params = parameters(tmp_path)
    first = execute_run(Downloader, **params, max_messages=2)
    assert first.success
    assert first.message_count == 2
    assert first.parity_status == "passed"
    assert first.termination_reason == "message_limit"
    second = execute_run(Downloader, **params)
    assert second.success
    assert second.message_count == 2
    assert second.parity_status == "passed"
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [item["message_id"] for item in rows] == ["1", "2", "3", "4"]
    state = json.loads((tmp_path / "checkpoint.json").read_text())
    assert state["total"] == 4
    assert state["completed"]
    assert state["resets"] == [3]
    final = execute_run(Downloader, **params)
    assert final.success
    assert final.message_count == 0


def test_interrupted_capture_checkpoints_only_written_records(tmp_path) -> None:
    class Interrupted(Downloader):
        records: ClassVar[list] = [message(1, 10)]
        failure = KeyboardInterrupt()

    params = parameters(tmp_path)
    result = execute_run(Interrupted, **params)
    assert result.interrupted
    assert not result.success
    assert json.loads((tmp_path / "checkpoint.json").read_text())["total"] == 1
    assert execute_run(Downloader, **params).message_count == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [("message_groups", ["all"]), ("end_time", 50), ("url", "other")],
)
def test_resume_rejects_changed_request_without_touching_outputs(
    tmp_path, saved_capture, field, value
) -> None:
    params = saved_capture
    before = (tmp_path / "chat.jsonl").read_bytes()
    params[field] = value
    result = execute_run(Downloader, **params)
    assert not result.success
    assert (tmp_path / "chat.jsonl").read_bytes() == before


def test_resume_rejects_modified_file_and_preserves_checkpoint(
    tmp_path, saved_capture
) -> None:
    params = saved_capture
    checkpoint = (tmp_path / "checkpoint.json").read_bytes()
    with (tmp_path / "chat.txt").open("a") as stream:
        stream.write("changed\n")
    result = execute_run(Downloader, **params)
    assert not result.success
    assert (tmp_path / "checkpoint.json").read_bytes() == checkpoint


@pytest.mark.parametrize(
    "outputs", [None, ["{title}.jsonl"], ["same.txt", "same.txt"], ["a.txt", "b.txt"]]
)
def test_checkpoint_requires_explicit_distinct_output_paths(tmp_path, outputs) -> None:
    with pytest.raises(ValueError):
        CaptureCheckpoint(str(tmp_path / "checkpoint"), {"output": outputs})


def test_new_checkpoint_refuses_existing_outputs_and_links(tmp_path) -> None:
    output = tmp_path / "existing.jsonl"
    output.write_text("valuable data")
    with pytest.raises(ValueError, match="absent"):
        CaptureCheckpoint(str(tmp_path / "checkpoint"), {"output": str(output)})
    link = tmp_path / "link"
    link.symlink_to(output)
    with pytest.raises(ValueError, match="regular"):
        CaptureCheckpoint(str(link), {"output": str(tmp_path / "new.jsonl")})


@pytest.fixture
def saved_capture(tmp_path):
    params = parameters(tmp_path)
    assert execute_run(Downloader, **params, max_messages=1).success
    return params


@pytest.mark.parametrize(
    "patch",
    [
        {"offset": float("nan")},
        {"ids": [1]},
        {"total": -1},
        {"chat_id": None},
        {"chat_id": "different"},
        {"record_loss": "false"},
        {"resets": [0]},
        {"resets": [2, 1]},
    ],
)
def test_checkpoint_rejects_corrupt_boundary_state(
    tmp_path, saved_capture, patch
) -> None:
    params = saved_capture
    path = tmp_path / "checkpoint.json"
    state = json.loads(path.read_text())
    state.update(patch)
    path.write_text(json.dumps(state))
    assert not execute_run(Downloader, **params).success


def test_resume_rejects_live_before_output(tmp_path) -> None:
    params = parameters(tmp_path)

    class Live(Downloader):
        status = "live"

    result = execute_run(Live, **params)
    assert not result.success
    assert not (tmp_path / "chat.jsonl").exists()


@pytest.mark.parametrize(
    ("records", "boundary_limit", "count", "offset"),
    [
        ([{"message_id": "1", "message_type": "text_message"}], 10_000, 0, None),
        ([message(1, 11), message(2, 10)], 10_000, 1, 11),
        (Downloader.records, 1, 1, 10),
    ],
    ids=["missing-offset", "backwards-offset", "timestamp-capacity"],
)
def test_invalid_replay_checkpoints_only_safe_prefix(
    tmp_path, monkeypatch, records, boundary_limit, count, offset
) -> None:
    monkeypatch.setattr(Downloader, "records", records)
    monkeypatch.setattr(
        "chat_downloader.runtime.capture_checkpoint._BOUNDARY_LIMIT", boundary_limit
    )
    result = execute_run(Downloader, **parameters(tmp_path))
    assert not result.success
    assert result.message_count == count
    if count:
        state = json.loads((tmp_path / "checkpoint.json").read_text())
        assert state["offset"] == offset
        assert state["total"] == count
    assert (tmp_path / "chat.jsonl").exists() == bool(count)


def test_atomic_checkpoint_failure_keeps_previous_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "checkpoint"
    path.write_text("previous")

    def fail(*args):
        raise OSError("sync failed")

    monkeypatch.setattr("chat_downloader.runtime.capture_checkpoint.os.fsync", fail)
    with pytest.raises(OSError):
        _atomic_json(path, {"new": "state"})
    assert path.read_text() == "previous"
    assert not list(tmp_path.glob(".capture-*"))


def test_verify_requires_pair_and_checkpoint_for_append(tmp_path) -> None:
    assert not execute_run(
        Downloader, verify_output=True, output=str(tmp_path / "one.jsonl")
    ).success
    params = parameters(tmp_path)
    params.pop("resume")
    assert not execute_run(Downloader, **params, overwrite=False).success
    assert execute_run(Downloader, **params).parity_status == "passed"


def test_parity_failure_does_not_advance_checkpoint(tmp_path, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ValueError("parity failed")

    monkeypatch.setattr("chat_downloader.runtime.runner.verify_capture", fail)
    result = execute_run(Downloader, **parameters(tmp_path))
    assert not result.success
    assert result.parity_status == "failed"
    assert not (tmp_path / "checkpoint.json").exists()


def test_empty_capture_has_distinct_success_and_parity_status(tmp_path) -> None:
    class Empty(Downloader):
        records: ClassVar[list] = []

    result = execute_run(Empty, **parameters(tmp_path))
    assert result.success
    assert result.parity_status == "passed"
    assert result.message_count == 0
    assert not (tmp_path / "chat.jsonl").exists()


def test_verifier_rejects_missing_writers() -> None:
    with pytest.raises(ValueError):
        verify_capture(Chat(iter([])))


def test_checkpoint_excludes_concurrent_runs_and_releases_lock(tmp_path) -> None:
    from chat_downloader.runtime.capture_checkpoint import checkpoint_lock

    params = parameters(tmp_path)
    with checkpoint_lock(params["resume"]):
        result = execute_run(Downloader, **params)
        assert not result.success
        assert not (tmp_path / "chat.jsonl").exists()
    assert execute_run(Downloader, **params).success
    assert not Path(params["resume"] + ".lock").exists()


def test_checkpoint_source_preserves_deadline_summary_and_unstarted_close(
    tmp_path,
) -> None:
    class Source:
        closed = False

        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            self.closed = True

        def deadline_prefetch_summary(self):
            return 3, False

    source = Source()
    chat = Chat(source, status="completed", id="id")
    checkpoint = CaptureCheckpoint(
        str(tmp_path / "checkpoint"), {"output": str(tmp_path / "chat.jsonl")}
    )
    checkpoint.bind(chat)
    assert chat.chat.deadline_prefetch_summary() == (3, False)
    assert iter(chat.chat) is chat.chat
    chat.close()
    chat.chat.close()
    assert source.closed


def test_checkpoint_detects_partial_writer_success(tmp_path, monkeypatch) -> None:
    from chat_downloader.output.continuous_write import ContinuousWriter

    original = ContinuousWriter.write

    def fail(self, item, **kwargs):
        if self.file_name.endswith(".txt"):
            raise OSError("text writer failed")
        return original(self, item, **kwargs)

    monkeypatch.setattr(ContinuousWriter, "write", fail)
    result = execute_run(Downloader, **parameters(tmp_path))
    assert not result.success
    assert not (tmp_path / "checkpoint.json").exists()


def test_zero_record_verification_cannot_pass_old_artifacts(tmp_path) -> None:
    params = parameters(tmp_path)
    params.pop("resume")
    assert execute_run(Downloader, **params).success

    class Empty(Downloader):
        records: ClassVar[list] = []

    result = execute_run(Empty, **params)
    assert not result.success
    assert "not produced by this run" in result.error_message


def test_verification_rejects_aliased_paths_before_writing(tmp_path) -> None:
    import os

    params = parameters(tmp_path)
    params.pop("resume")
    first = tmp_path / "chat.jsonl"
    first.write_text("keep me")
    os.link(first, tmp_path / "chat.txt")
    result = execute_run(Downloader, **params)
    assert not result.success
    assert first.read_text() == "keep me"


def test_verifier_reports_actual_text_mismatch(tmp_path) -> None:
    from chat_downloader.errors import ChatDownloaderError

    downloader = Downloader()
    chat = downloader.get_chat(
        output=[str(tmp_path / "chat.jsonl"), str(tmp_path / "chat.txt")], format="kick"
    )
    list(chat)
    (tmp_path / "chat.txt").write_text("corrupted\n")
    with pytest.raises(ChatDownloaderError, match="parity"):
        verify_capture(chat)


def test_verifier_expands_lazy_paths_once_and_keeps_braces_in_titles(tmp_path) -> None:
    chat = Chat(iter([message(1, 1)]), id="id", title="{literal}")
    request = ChatRequest(
        output=[str(tmp_path / "{title}.jsonl"), str(tmp_path / "{title}.txt")],
        format="kick",
    )
    configure_chat(chat, request, SimpleNamespace(is_live_status=lambda _: False))
    list(chat)
    verify_capture(chat)


def test_verifier_detects_identity_change_after_writers_are_attached(tmp_path) -> None:
    import os

    from chat_downloader.runtime.capture_verification import capture_paths

    first, second = tmp_path / "first.jsonl", tmp_path / "second.txt"
    chat = Downloader().get_chat(output=[str(first), str(second)], format="kick")
    first.write_text("preserve")
    os.link(first, second)
    with pytest.raises(ValueError, match="distinct"):
        capture_paths(chat)
    chat.close()
    assert first.read_text() == "preserve"

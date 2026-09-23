# SPDX-License-Identifier: MIT

"""Real writer/formatter tests for checkpointed replay and automatic parity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from chat_downloader.errors import ChatDownloaderError
from chat_downloader.models import ChatRequest
from chat_downloader.runtime.capture_checkpoint import CaptureCheckpoint, _atomic_json
from chat_downloader.runtime.capture_verification import capture_paths, verify_capture
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
    records: ClassVar[list] = [message(i, 10 if i < 4 else 11) for i in range(1, 5)]
    failure = None
    status = "completed"

    def __init__(self, **kwargs):
        pass

    def get_chat(self, **kwargs):
        request = ChatRequest.from_kwargs(**kwargs)

        def source():
            for item in self.records:
                if request.start_time is None or item.get(
                    "time_in_seconds", 0
                ) >= float(request.start_time):
                    yield item.copy()
            if self.failure is not None:
                raise self.failure

        chat = Chat(source(), id="video", status=self.status)
        configure_chat(
            chat,
            request,
            SimpleNamespace(
                is_live_status=lambda _: False,
                is_completed_replay_status=lambda status: status == "completed",
            ),
        )
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


@pytest.fixture(name="params")
def capture_params(tmp_path):
    return parameters(tmp_path)


def load_json(path):
    return json.loads(path.read_text())


def test_resume_preserves_same_timestamp_messages_and_verifies_both_runs(
    tmp_path,
    params,
) -> None:
    for options in ({"max_messages": 2}, {}):
        result = execute_run(Downloader, **params, **options)
        assert result.success
        assert result.message_count == 2
        assert result.parity_status == "passed"
        if options:
            assert result.termination_reason == "message_limit"
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [item["message_id"] for item in rows] == ["1", "2", "3", "4"]
    state = load_json(tmp_path / "checkpoint.json")
    assert state["total"] == 4
    assert state["completed"]
    assert state["resets"] == [3]
    final = execute_run(Downloader, **params)
    assert final.success
    assert final.message_count == 0


def test_resume_saves_checkpoint_when_formatted_writer_deduplicates_paid_item(
    tmp_path,
) -> None:
    paid = message(2, 10)
    paid["message_type"] = "paid_message"
    ticker = paid.copy()
    ticker["message_type"] = "ticker_paid_message_item"

    class PaidReplay(Downloader):
        records: ClassVar[list] = [message(1, 9), paid, ticker, message(3, 11)]

    params = parameters(tmp_path)
    first = execute_run(PaidReplay, **params, max_messages=3)
    assert first.success
    assert first.parity_status == "passed"
    assert load_json(tmp_path / "checkpoint.json")["total"] == 3
    assert execute_run(PaidReplay, **params).success


def test_resume_ignores_synthetic_chat_end_after_last_record(tmp_path) -> None:
    class EndedReplay(Downloader):
        records: ClassVar[list] = [
            message(1, 10),
            {
                "message_type": "chat_ended",
                "action_type": "chat_ended",
                "message": None,
            },
        ]

    result = execute_run(EndedReplay, **parameters(tmp_path))
    assert result.success
    assert result.message_count == 1
    assert load_json(tmp_path / "checkpoint.json")["completed"]


@pytest.mark.parametrize(
    ("kind", "patch"),
    [
        ("request", {"message_groups": ["all"]}),
        ("request", {"end_time": 50}),
        ("request", {"url": "other"}),
        ("output", None),
        *[
            ("checkpoint", patch)
            for patch in [
                {"offset": float("nan")},
                {"ids": [1]},
                {"total": -1},
                {"chat_id": None},
                {"chat_id": "different"},
                {"record_loss": "false"},
                {"resets": [0]},
                {"resets": [2, 1]},
            ]
        ],
    ],
)
def test_resume_rejects_changed_request_artifacts_or_boundary(
    tmp_path, saved_capture, kind, patch
):
    checkpoint = tmp_path / "checkpoint.json"
    output = tmp_path / "chat.jsonl"
    before = output.read_bytes()
    checkpoint_before = checkpoint.read_bytes()
    if kind == "request":
        saved_capture.update(patch)
    elif kind == "checkpoint":
        state = load_json(checkpoint)
        checkpoint.write_text(json.dumps({**state, **patch}))
    else:
        with (tmp_path / "chat.txt").open("a") as stream:
            stream.write("changed\n")
    assert not execute_run(Downloader, **saved_capture).success
    assert output.read_bytes() == before
    if kind == "output":
        assert checkpoint.read_bytes() == checkpoint_before


@pytest.mark.parametrize(
    "outputs",
    [None, ["{title}.jsonl"], ["same.txt", "same.txt"], ["a.txt", "b.txt"]],
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
def saved_capture(params):
    assert execute_run(Downloader, **params, max_messages=1).success
    return params


@pytest.mark.parametrize("checkpoint", [False, True])
def test_live_rejected_before_output(tmp_path, monkeypatch, checkpoint, params):
    monkeypatch.setattr(Downloader, "status", "live")
    if not checkpoint:
        params.pop("resume")
    assert not execute_run(
        Downloader, **params, require_complete=not checkpoint
    ).success
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
    tmp_path, monkeypatch, records, boundary_limit, count, offset, params
) -> None:
    monkeypatch.setattr(Downloader, "records", records)
    monkeypatch.setattr(
        "chat_downloader.runtime.capture_checkpoint._BOUNDARY_LIMIT", boundary_limit
    )
    result = execute_run(Downloader, **params)
    assert not result.success
    assert result.message_count == count
    if count:
        state = load_json(tmp_path / "checkpoint.json")
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


def test_verify_requires_pair_and_checkpoint_for_append(tmp_path, params) -> None:
    assert not execute_run(
        Downloader, verify_output=True, output=str(tmp_path / "one.jsonl")
    ).success
    params.pop("resume")
    assert not execute_run(Downloader, **params, overwrite=False).success
    assert execute_run(Downloader, **params).parity_status == "passed"


@pytest.mark.parametrize("failure", ["parity", "writer"])
def test_failed_capture_does_not_advance_checkpoint(
    tmp_path, monkeypatch, failure, params
):
    from chat_downloader.output.continuous_write import ContinuousWriter

    original = ContinuousWriter.write

    def fail(*args, **kwargs):
        if failure == "parity":
            raise ValueError("parity failed")
        if args[0].file_name.endswith(".txt"):
            raise OSError("text writer failed")
        return original(*args, **kwargs)

    if failure == "parity":
        monkeypatch.setattr("chat_downloader.runtime.runner.verify_capture", fail)
    else:
        monkeypatch.setattr(ContinuousWriter, "write", fail)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert (tmp_path / "checkpoint.json").exists() is (failure == "writer")
    if failure == "writer":
        assert load_json(tmp_path / "checkpoint.json")["total"] == 0
    if failure == "parity":
        assert result.parity_status == "failed"


@pytest.mark.parametrize("interrupt_at", ["txt_write", "checkpoint_observe"])
def test_interrupted_record_rolls_back_before_resume(
    tmp_path, monkeypatch, params, interrupt_at
) -> None:
    from chat_downloader.output.continuous_write import ContinuousWriter

    if interrupt_at == "txt_write":
        original_write = ContinuousWriter.write

        def interrupted_write(writer, item, *, flush=False):
            if writer.file_name.endswith(".txt") and "Record 2" in str(item):
                raise KeyboardInterrupt
            original_write(writer, item, flush=flush)

        monkeypatch.setattr(ContinuousWriter, "write", interrupted_write)
    else:
        original_observe = CaptureCheckpoint.observe

        def interrupted_observe(checkpoint, item):
            original_observe(checkpoint, item)
            if item["message_id"] == "2":
                raise KeyboardInterrupt

        monkeypatch.setattr(CaptureCheckpoint, "observe", interrupted_observe)

    first = execute_run(Downloader, **params)
    assert first.interrupted
    assert load_json(tmp_path / "checkpoint.json")["total"] == 1
    assert len((tmp_path / "chat.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 1

    monkeypatch.undo()
    resumed = execute_run(Downloader, **params)
    assert resumed.success
    lines = (tmp_path / "chat.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [item["message_id"] for item in rows] == ["1", "2", "3", "4"]


def test_negative_replay_offsets_can_resume(tmp_path, monkeypatch, params) -> None:
    records = [message(1, -15), message(2, -14), message(3, 1)]
    monkeypatch.setattr(Downloader, "records", records)
    first = execute_run(Downloader, **params, max_messages=1)
    assert first.success
    assert load_json(tmp_path / "checkpoint.json")["offset"] == -15
    second = execute_run(Downloader, **params)
    assert second.success
    lines = (tmp_path / "chat.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [item["message_id"] for item in rows] == ["1", "2", "3"]


@pytest.mark.parametrize("error_type", [RuntimeError, OSError, ValueError])
def test_failed_chat_close_does_not_advance_checkpoint(
    tmp_path, params, error_type
) -> None:
    class FailedClose(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            original_close = chat.close

            def close() -> None:
                original_close()
                raise error_type("close failed")

            chat.close = close
            return chat

    result = execute_run(FailedClose, **params)
    assert not result.success
    assert result.error_message == "close failed"
    assert not (tmp_path / "checkpoint.json").exists()


def test_failed_chat_close_preserves_primary_error_without_checkpoint(
    tmp_path, params
) -> None:
    class FailedClose(Downloader):
        failure = RuntimeError("stream failed")

        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            original_close = chat.close

            def close() -> None:
                original_close()
                raise OSError("close failed")

            chat.close = close
            return chat

    result = execute_run(FailedClose, **params)
    assert not result.success
    assert result.error_message == "stream failed"
    assert not (tmp_path / "checkpoint.json").exists()


def test_checkpoint_fingerprint_changes_with_program_version(
    tmp_path, monkeypatch
) -> None:
    import chat_downloader.runtime.capture_checkpoint as module

    request = {"output": str(tmp_path / "capture.jsonl")}
    first = CaptureCheckpoint(str(tmp_path / "one.json"), request.copy())
    monkeypatch.setattr(module, "__version__", "future-test-version", raising=False)
    second = CaptureCheckpoint(str(tmp_path / "two.json"), request.copy())
    assert first.fingerprint != second.fingerprint


def test_checkpoint_fingerprint_changes_with_builtin_formats(
    tmp_path, monkeypatch
) -> None:
    import chat_downloader.runtime.capture_checkpoint as module

    package = tmp_path / "chat_downloader"
    runtime = package / "runtime"
    runtime.mkdir(parents=True)
    formats = package / "formatting"
    formats.mkdir()
    builtin = formats / "custom_formats.json"
    builtin.write_text('{"default": "first"}', encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(runtime / "capture_checkpoint.py"))
    request = {"output": str(tmp_path / "capture.jsonl")}
    first = CaptureCheckpoint(str(tmp_path / "one.json"), request.copy())
    builtin.write_text('{"default": "second"}', encoding="utf-8")
    second = CaptureCheckpoint(str(tmp_path / "two.json"), request.copy())
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize("identity_change", ["version", "builtin_formats"])
def test_resume_rejects_changed_build_identity_before_append(
    tmp_path, monkeypatch, saved_capture, identity_change
) -> None:
    import chat_downloader.runtime.capture_checkpoint as module

    output = tmp_path / "chat.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    before_output, before_checkpoint = output.read_bytes(), checkpoint.read_bytes()
    if identity_change == "version":
        monkeypatch.setattr(module, "__version__", "future-test-version")
    else:
        package = tmp_path / "chat_downloader"
        runtime = package / "runtime"
        runtime.mkdir(parents=True)
        formats = package / "formatting"
        formats.mkdir()
        builtin = formats / "custom_formats.json"
        original = (
            Path(module.__file__).resolve().parents[1]
            / "formatting"
            / "custom_formats.json"
        )
        builtin.write_bytes(original.read_bytes() + b"\n")
        monkeypatch.setattr(module, "__file__", str(runtime / "capture_checkpoint.py"))

    result = execute_run(Downloader, **saved_capture)
    assert not result.success
    assert "Checkpoint does not match" in result.error_message
    assert output.read_bytes() == before_output
    assert checkpoint.read_bytes() == before_checkpoint


def test_checkpoint_record_snapshot_requires_writers(tmp_path) -> None:
    checkpoint = CaptureCheckpoint(
        str(tmp_path / "checkpoint.json"),
        {"output": str(tmp_path / "capture.jsonl")},
    )
    chat = Chat(iter(()))
    with pytest.raises(ChatDownloaderError, match="attached output writers"):
        checkpoint.begin_record(chat)
    with pytest.raises(ChatDownloaderError, match="output records disagree"):
        checkpoint.save(chat, 0)


@pytest.mark.parametrize("stale", [False, True])
def test_empty_capture_distinguishes_absent_and_stale_artifacts(
    tmp_path, monkeypatch, stale, params
):
    if stale:
        params.pop("resume")
        assert execute_run(Downloader, **params).success
    monkeypatch.setattr(Downloader, "records", [])
    result = execute_run(Downloader, **params)
    assert result.success is not stale
    assert result.message_count == 0
    if stale:
        assert "not produced by this run" in result.error_message
    else:
        assert result.parity_status == "passed"
        assert not (tmp_path / "chat.jsonl").exists()


def test_verifier_rejects_missing_writers() -> None:
    with pytest.raises(ValueError):
        verify_capture(Chat(iter([])))


def test_checkpoint_excludes_concurrent_runs_and_releases_lock(tmp_path, params):
    from chat_downloader.runtime.capture_checkpoint import checkpoint_lock

    with checkpoint_lock(params["resume"]):
        result = execute_run(Downloader, **params)
        assert not result.success
        assert not (tmp_path / "chat.jsonl").exists()
    assert execute_run(Downloader, **params).success
    assert not Path(params["resume"] + ".lock").exists()


def test_resume_reports_missing_checkpoint_directory_before_capture(tmp_path, params):
    missing = tmp_path / "missing"
    params["resume"] = str(missing / "checkpoint.json")

    result = execute_run(Downloader, **params)

    assert not result.success
    assert result.error_message == (
        f"Resume checkpoint parent directory must already exist: {missing}"
    )
    assert not missing.exists()
    assert not (tmp_path / "chat.jsonl").exists()


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
    chat.site = SimpleNamespace(
        is_completed_replay_status=lambda status: status == "completed"
    )
    checkpoint = CaptureCheckpoint(
        str(tmp_path / "checkpoint"), {"output": str(tmp_path / "chat.jsonl")}
    )
    checkpoint.bind(chat)
    assert chat.chat.deadline_prefetch_summary() == (3, False)
    assert iter(chat.chat) is chat.chat
    chat.close()
    chat.chat.close()
    assert source.closed


@pytest.mark.parametrize("attached", [False, True])
def test_verification_rejects_aliased_paths_without_writing(tmp_path, attached, params):
    params.pop("resume")
    first, second = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    if attached:
        chat = Downloader().get_chat(**params)
    first.write_text("preserve")
    os.link(first, second)
    if attached:
        with pytest.raises(ValueError, match="distinct"):
            capture_paths(chat)
        chat.close()
    else:
        assert not execute_run(Downloader, **params).success
    assert first.read_text() == "preserve"


@pytest.mark.parametrize("corrupted", [False, True])
def test_verifier_expands_lazy_paths_and_reports_text_mismatch(tmp_path, corrupted):
    from chat_downloader.errors import ChatDownloaderError

    chat = Chat(iter([message(1, 1)]), id="id", title="{literal}")
    request = ChatRequest(
        output=[str(tmp_path / "{title}.jsonl"), str(tmp_path / "{title}.txt")],
        format="kick",
    )
    configure_chat(
        chat,
        request,
        SimpleNamespace(
            is_live_status=lambda _: False,
            is_completed_replay_status=lambda status: status == "completed",
        ),
    )
    list(chat)
    if corrupted:
        (tmp_path / "{literal}.txt").write_text("corrupted\n")
        with pytest.raises(ChatDownloaderError, match="parity"):
            verify_capture(chat)
    else:
        verify_capture(chat)

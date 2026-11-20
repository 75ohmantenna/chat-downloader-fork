# SPDX-License-Identifier: MIT

"""Real writer/formatter tests for checkpointed replay and automatic parity."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from chat_downloader.errors import ChatDownloaderError
from chat_downloader.formatting import format as format_module
from chat_downloader.models import ChatRequest
from chat_downloader.runtime import capture_checkpoint as checkpoints
from chat_downloader.runtime.capture_checkpoint import CaptureCheckpoint, _atomic_json
from chat_downloader.runtime.capture_replay_order import ordered_initial_preroll
from chat_downloader.runtime.capture_verification import capture_paths, verify_capture
from chat_downloader.runtime.chat_pipeline import configure_chat
from chat_downloader.runtime.cli_bridge import categorize_parameters
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.filters import MessageFilter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.continuations import parse_continuation_response
from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action


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
    monkeypatch,
) -> None:
    logs = []
    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, text: logs.append((level, text)),
    )
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
    assert [entry for entry in logs if entry[1].startswith("Resume checkpoint")] == [
        (
            "debug",
            "Resume checkpoint enforces max_messages=2 after overlap suppression.",
        )
    ]


def test_resume_orders_initial_zero_notice_after_negative_preroll(
    tmp_path, params
) -> None:
    notice = message(0, 0)
    notice["message_type"] = "viewer_engagement_message"

    class PrerollReplay(Downloader):
        records: ClassVar[list] = [
            notice,
            message(1, -13),
            message(2, -12),
            message(3, 0),
            message(4, 1),
        ]

    first = execute_run(PrerollReplay, **params, max_messages=2)
    assert first.success
    assert first.parity_status == "passed"
    assert load_json(tmp_path / "checkpoint.json")["offset"] == -12

    second = execute_run(PrerollReplay, **params, require_complete=True)
    assert second.success
    assert second.parity_status == "passed"
    assert load_json(tmp_path / "checkpoint.json")["completed"]
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [item["message_id"] for item in rows] == ["1", "2", "0", "3", "4"]
    assert [item["time_in_seconds"] for item in rows] == [-13, -12, 0, 0, 1]


def test_replay_pinned_banner_resumes_with_verified_outputs(tmp_path):
    from chat_downloader.sites.filters import MessageFilter
    from chat_downloader.sites.youtube.constants_message import _MESSAGE_GROUPS
    from chat_downloader.sites.youtube.continuations import parse_continuation_response
    from chat_downloader.sites.youtube.message_pipeline import process_pipeline_action

    fixture = (
        Path(__file__).parent
        / "fixtures/youtube/live_events/youtube-replay-pinned-banner-timing.json"
    )
    response = parse_continuation_response(load_json(fixture))
    message_filter = MessageFilter(_MESSAGE_GROUPS, groups_to_add=["all"])

    class BannerReplay(Downloader):
        records: ClassVar[list] = [
            process_pipeline_action(action, 0, message_filter, None).message
            for action in response.actions
        ]

    params = parameters(tmp_path)
    params["format"] = "youtube"
    first = execute_run(BannerReplay, **params, max_messages=1)
    assert first.success
    assert first.parity_status == "passed"
    second = execute_run(BannerReplay, **params, require_complete=True)
    assert second.success
    assert second.message_count == 1
    assert second.parity_status == "passed"
    state = load_json(tmp_path / "checkpoint.json")
    assert state["completed"]
    assert state["offset"] == 17330.715
    assert state["total"] == 2
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [item["message_type"] for item in rows] == ["text_message", "banner"]
    assert rows[1]["timestamp"] == 1791910497630202
    assert rows[1]["time_text"] == "4:48:50"
    assert rows[1]["author"]["is_owner"]
    assert execute_run(BannerReplay, **params, require_complete=True).message_count == 0


def test_initial_zero_notice_buffer_respects_checkpoint_boundary() -> None:
    notices = [message(1, 0), message(2, 0)]
    with pytest.raises(ValueError, match="too many message IDs"):
        list(ordered_initial_preroll(notices, boundary_limit=1))


def test_preroll_order_does_not_restart_reusable_source() -> None:
    records = [message(1, 0), message(2, -1), message(3, 1), message(4, 2)]
    assert list(ordered_initial_preroll(records, boundary_limit=10)) == [
        records[1],
        records[0],
        records[2],
        records[3],
    ]


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


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_resume_preserves_paid_ticker_events_and_repeated_occurrences(tmp_path, limit):
    fixture = Path(__file__).parent / "fixtures/youtube/live_events"
    payload = load_json(fixture / "youtube-replay-paid-ticker-shared-offset.json")
    parsed_records = [
        process_pipeline_action(action, 0, MessageFilter({}), None).message
        for action in parse_continuation_response(payload).actions
    ]
    assert all(parsed_records)

    class PaidReplay(Downloader):
        # Include repeated events and different actions sharing the same ID/type.
        records: ClassVar[list] = [
            parsed_records[0],
            parsed_records[1],
            parsed_records[1],
            {**parsed_records[1], "action_type": "replace_chat_item"},
        ]

    params = parameters(tmp_path)
    assert execute_run(PaidReplay, **params, max_messages=limit).success
    resumed = execute_run(PaidReplay, **params, require_complete=True)
    assert resumed.success
    assert resumed.parity_status == "passed"
    assert resumed.message_count == 4 - limit
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert rows == PaidReplay.records
    assert execute_run(PaidReplay, **params).message_count == 0


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
                {"version": 1},
                {"events": [1]},
                {"events": {"bad": 0}},
                {"events": {"bad": True}},
                {"events": {"bad": 1}},
                {"events": {}},
                {"offset": None},
                {"total": 0},
                {"events": {"0" * 64: 2}},
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
    [None, ["{title}.jsonl"], ["a.txt", "b.txt"]],
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
        (Downloader.records, 1, 1, 10),
    ],
    ids=["missing-offset", "timestamp-capacity"],
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
        original_observe = CaptureCheckpoint._observe
        second_event = checkpoints._event_identity(Downloader.records[1])

        def interrupted_observe(checkpoint, offset, event):
            original_observe(checkpoint, offset, event)
            if event == second_event:
                raise KeyboardInterrupt

        monkeypatch.setattr(CaptureCheckpoint, "_observe", interrupted_observe)

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


@pytest.mark.parametrize("resumed", [False, True])
def test_failed_record_recovery_never_certifies_torn_files(
    tmp_path, monkeypatch, params, resumed
):
    from chat_downloader.output.continuous_write import ContinuousWriter

    params["verify_output"] = False
    checkpoint_path = tmp_path / "checkpoint.json"
    previous = None
    if resumed:
        assert execute_run(Downloader, **params, max_messages=1).success
        previous = checkpoint_path.read_bytes()
    original_write = ContinuousWriter.write
    original_truncate = os.ftruncate
    torn_record = False

    def torn_write(writer, item, *, flush=False):
        nonlocal torn_record
        original_write(writer, item, flush=flush)
        if writer.file_name.endswith(".jsonl") and item["message_id"] == "2":
            torn_record = True
            raise RuntimeError("failure after appending before acceptance")

    def failed_truncation(descriptor, size):
        info = os.fstat(descriptor)
        raw = (tmp_path / "chat.jsonl").stat()
        if torn_record and (info.st_dev, info.st_ino) == (raw.st_dev, raw.st_ino):
            raise OSError("record recovery denied")
        return original_truncate(descriptor, size)

    monkeypatch.setattr(ContinuousWriter, "write", torn_write)
    monkeypatch.setattr(os, "ftruncate", failed_truncation)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert result.error_message == "record recovery denied"
    assert len((tmp_path / "chat.jsonl").read_text().splitlines()) == 2
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 1
    if previous is None:
        assert not checkpoint_path.exists()
    else:
        assert checkpoint_path.read_bytes() == previous


def test_interrupted_acceptance_count_restores_safe_checkpoint(
    tmp_path, monkeypatch, params
):
    from chat_downloader.runtime import runner

    class InterruptingResult(runner.RunResult):
        def __setattr__(self, name, value):
            if name == "message_count" and value == 2:
                raise KeyboardInterrupt
            super().__setattr__(name, value)

    monkeypatch.setattr(runner, "RunResult", InterruptingResult)
    result = execute_run(Downloader, **params)
    assert result.interrupted
    assert result.message_count == 1
    assert load_json(tmp_path / "checkpoint.json")["total"] == 1
    assert len((tmp_path / "chat.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 1


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


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("error_type", [RuntimeError, OSError, ValueError])
def test_failed_chat_close_does_not_advance_checkpoint(
    tmp_path, params, error_type, resumed
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

    checkpoint = tmp_path / "checkpoint.json"
    if resumed:
        assert execute_run(Downloader, **params, max_messages=1).success
        before = checkpoint.read_bytes()

    result = execute_run(FailedClose, **params)
    assert not result.success
    assert result.error_message == "close failed"
    if resumed:
        assert checkpoint.read_bytes() == before
    else:
        assert not checkpoint.exists()


@pytest.mark.parametrize("resumed", [False, True])
def test_failed_downloader_close_does_not_advance_checkpoint(
    tmp_path, params, resumed
) -> None:
    class FailedClose(Downloader):
        def close(self) -> None:
            raise RuntimeError("downloader close failed")

    checkpoint = tmp_path / "checkpoint.json"
    if resumed:
        assert execute_run(Downloader, **params, max_messages=1).success
        before = checkpoint.read_bytes()

    result = execute_run(FailedClose, **params)
    assert not result.success
    assert result.error_message == "downloader close failed"
    if resumed:
        assert checkpoint.read_bytes() == before
    else:
        assert not checkpoint.exists()


def test_suppressed_provider_close_error_allows_checkpoint_to_advance(
    tmp_path, params, monkeypatch
):
    from chat_downloader import ChatDownloader

    warnings = []
    monkeypatch.setattr(
        "chat_downloader.runtime.session_lifecycle.log",
        lambda level, message: warnings.append((level, message)),
    )

    def fail_to_close():
        raise OSError("provider close failed")

    class Replay(Downloader):
        def close(self):
            owner = ChatDownloader()
            owner.sessions["provider"] = SimpleNamespace(close=fail_to_close)
            owner.close()

    assert execute_run(Downloader, **params, max_messages=1).success
    checkpoint = tmp_path / "checkpoint.json"
    assert load_json(checkpoint)["total"] == 1

    assert execute_run(Replay, **params).success
    assert load_json(checkpoint)["total"] == len(Replay.records)
    assert ("warning", "Error closing session: provider close failed") in warnings


def test_failed_downloader_close_preserves_primary_error_without_checkpoint(
    tmp_path, params
) -> None:
    class FailedClose(Downloader):
        failure = RuntimeError("stream failed")

        def close(self) -> None:
            raise RuntimeError("downloader close failed")

    result = execute_run(FailedClose, **params)
    assert not result.success
    assert result.error_message == "stream failed"
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


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("stream_error", [False, True])
def test_writer_close_failure_preserves_first_error_and_checkpoint(
    tmp_path, monkeypatch, params, resumed, stream_error
) -> None:
    from chat_downloader.output.continuous_write import ContinuousWriter

    checkpoint = tmp_path / "checkpoint.json"
    if resumed:
        assert execute_run(Downloader, **params, max_messages=1).success
    before = checkpoint.read_bytes() if resumed else None

    original_close = ContinuousWriter.close

    def fail_after_close(writer) -> None:
        original_close(writer)
        if writer.file_name.endswith(".txt"):
            raise OSError("writer close failed")

    monkeypatch.setattr(ContinuousWriter, "close", fail_after_close)

    class Replay(Downloader):
        failure = RuntimeError("stream failed") if stream_error else None

    result = execute_run(Replay, **params)
    assert not result.success
    expected = "stream failed" if stream_error else "output writer(s) reported errors"
    assert expected in result.error_message
    assert result.parity_status == "not_run"
    if resumed:
        assert checkpoint.read_bytes() == before
    else:
        assert not checkpoint.exists()


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
    builtin = tmp_path / "custom_formats.json"
    builtin.write_text('{"default": "first"}', encoding="utf-8")
    monkeypatch.setattr(format_module, "BUILTIN_FORMAT_FILE", builtin)
    request = {"output": str(tmp_path / "capture.jsonl")}
    first = CaptureCheckpoint(str(tmp_path / "one.json"), request.copy())
    builtin.write_text('{"default": "second"}', encoding="utf-8")
    second = CaptureCheckpoint(str(tmp_path / "two.json"), request.copy())
    assert first.fingerprint != second.fingerprint


def test_linked_builtin_formats_can_resume(tmp_path, monkeypatch, params) -> None:
    linked = tmp_path / "builtin_formats.json"
    linked.symlink_to(format_module.BUILTIN_FORMAT_FILE)
    monkeypatch.setattr(format_module, "BUILTIN_FORMAT_FILE", linked)
    assert execute_run(Downloader, **params, max_messages=1).success
    assert execute_run(Downloader, **params).success


def test_missing_builtin_formats_rejects_capture_before_output(
    tmp_path, monkeypatch, params
) -> None:
    monkeypatch.setattr(
        format_module, "BUILTIN_FORMAT_FILE", tmp_path / "missing_formats.json"
    )
    result = execute_run(Downloader, **params)
    assert not result.success
    assert "built-in formats" in result.error_message
    assert not (tmp_path / "checkpoint.json").exists()
    assert not (tmp_path / "chat.jsonl").exists()


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
        builtin = tmp_path / "custom_formats.json"
        original = format_module.BUILTIN_FORMAT_FILE
        builtin.write_bytes(original.read_bytes() + b"\n")
        monkeypatch.setattr(format_module, "BUILTIN_FORMAT_FILE", builtin)

    result = execute_run(Downloader, **saved_capture)
    assert not result.success
    assert "Checkpoint does not match" in result.error_message
    assert "program version" in result.error_message
    assert output.read_bytes() == before_output
    assert checkpoint.read_bytes() == before_checkpoint


def test_checkpoint_record_snapshot_requires_writers(tmp_path) -> None:
    checkpoint = CaptureCheckpoint(
        str(tmp_path / "checkpoint.json"),
        {"output": str(tmp_path / "capture.jsonl")},
    )
    chat = Chat(iter(()))
    with pytest.raises(ChatDownloaderError, match="attached output writers"):
        checkpoint.advance(chat, SimpleNamespace(message_count=0))
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
        _, chat_parameters, _ = categorize_parameters(params)
        chat = Downloader().get_chat(**chat_parameters)
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


@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("with_manifest", [False, True])
@pytest.mark.parametrize("replacement", ["regular", "missing"])
def test_checkpoint_does_not_adopt_artifacts_replaced_after_close(
    tmp_path, params, exhausted, with_manifest, replacement
):
    first_options = {} if exhausted else {"max_messages": 2}
    assert execute_run(Downloader, **params, **first_options).success
    checkpoint = Path(params["resume"])
    prior_checkpoint = checkpoint.read_bytes()
    outputs = [Path(name) for name in params["output"]]

    class Replaced(Downloader):
        def close(self):
            for output in outputs:
                output.rename(output.with_suffix(output.suffix + ".original"))
                if replacement == "regular":
                    output.write_text("", encoding="utf-8")

    report = tmp_path / "replacement.json"
    options = {"run_manifest": str(report)} if with_manifest else {}
    result = execute_run(Replaced, **params, **options)
    assert not result.success
    assert result.message_count == (0 if exhausted else 2)
    assert checkpoint.read_bytes() == prior_checkpoint
    if report.exists():
        assert not load_json(report)["success"]


def test_exhausted_resume_rejects_in_place_changes(tmp_path, params):
    assert execute_run(Downloader, **params).success
    checkpoint = Path(params["resume"])
    previous = checkpoint.read_bytes()

    class Cleared(Downloader):
        def close(self):
            for name in params["output"]:
                Path(name).write_text("", encoding="utf-8")

    report = tmp_path / "cleared.json"
    result = execute_run(Cleared, **params, run_manifest=str(report))
    assert not result.success
    assert result.message_count == 0
    assert checkpoint.read_bytes() == previous
    assert not report.exists()


def test_new_checkpoint_rejects_output_created_before_first_record(tmp_path, params):
    class Arrival(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            source = chat.chat

            def arriving():
                for name in params["output"]:
                    Path(name).write_text("UNRELATED_CAPTURE", encoding="utf-8")
                yield from source

            chat.chat = arriving()
            return chat

    result = execute_run(Arrival, **params)
    assert not result.success
    assert result.message_count == 0
    assert not Path(params["resume"]).exists()
    assert all(
        Path(name).read_text(encoding="utf-8") == "UNRELATED_CAPTURE"
        for name in params["output"]
    )


@pytest.mark.parametrize("resumed", [False, True])
def test_backwards_offset_never_publishes_new_boundary(tmp_path, params, resumed):
    class Backwards(Downloader):
        records: ClassVar[list] = [
            message(1, 10),
            message(2, 11),
            message(3, 9),
            message(4, 12),
        ]

    path = Path(params["resume"])
    if resumed:
        assert execute_run(Backwards, **params, max_messages=1).success
    previous = path.read_bytes() if resumed else None

    first = execute_run(Backwards, **params)
    assert not first.success
    assert "offsets moved backwards" in first.error_message
    assert (path.read_bytes() if path.exists() else None) == previous

    second = execute_run(Backwards, **params)
    assert not second.success
    assert second.message_count == 0
    assert "requires absent output files" in second.error_message or (
        "Capture files changed since the checkpoint" in second.error_message
    )


def test_checkpoint_destination_that_aliases_opened_output_is_rejected(
    tmp_path, params, monkeypatch
):
    path = Path(params["resume"])
    output = Path(params["output"][0])
    identity = checkpoints.output_path_identity

    def case_insensitive_alias(candidate):
        return identity(output if candidate == path and output.exists() else candidate)

    monkeypatch.setattr(checkpoints, "output_path_identity", case_insensitive_alias)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert "paths must be distinct" in result.error_message
    assert not path.exists()
    assert len(output.read_text().splitlines()) == len(Downloader.records)


@pytest.mark.parametrize("resumed", [False, True])
def test_checkpoint_preserves_destination_substituted_during_capture(
    tmp_path, params, resumed
):
    path = Path(params["resume"])
    if resumed:
        assert execute_run(Downloader, **params, max_messages=1).success

    class Substituted(Downloader):
        def close(self):
            replacement = tmp_path / "replacement"
            replacement.write_text("unrelated")
            replacement.replace(path)

    result = execute_run(Substituted, **params)
    assert not result.success
    assert path.read_text() == "unrelated"


def test_lock_close_interrupt_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint"
    close = os.close

    def interrupt(descriptor):
        close(descriptor)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", interrupt)
        with pytest.raises(KeyboardInterrupt), checkpoints.checkpoint_lock(str(path)):
            pytest.fail("The lock close should have interrupted entry.")
    assert not path.with_suffix(".lock").exists()


def test_lock_creation_signal_is_deferred_until_entry_is_registered(
    tmp_path, params, monkeypatch
):
    from chat_downloader.utils.interrupts import raise_or_defer_interrupt

    lock = params["resume"] + ".lock"
    open_file = os.open

    def interrupt(path, *args, **kwargs):
        descriptor = open_file(path, *args, **kwargs)
        if str(path) == lock:
            raise_or_defer_interrupt()
        return descriptor

    monkeypatch.setattr(os, "open", interrupt)
    result = execute_run(Downloader, **params)
    assert result.interrupted
    assert not Path(lock).exists()
    assert not (tmp_path / "chat.jsonl").exists()


@pytest.mark.parametrize("replacement", ["", "other content " * 100])
def test_resumed_writer_rejects_changes_to_verified_prefix(params, replacement):
    assert execute_run(Downloader, **params, max_messages=1).success
    path = Path(params["resume"])
    previous = path.read_bytes()

    class Rewritten(Downloader):
        def close(self):
            Path(params["output"][0]).write_text(replacement)

    result = execute_run(Rewritten, **{**params, "verify_output": False})
    assert not result.success
    assert "Capture files changed after checkpoint verification" in result.error_message
    assert path.read_bytes() == previous


def test_checkpoint_syncs_new_output_directory_before_publication(
    tmp_path, params, monkeypatch
):
    capture = tmp_path / "capture" / "nested"
    params["output"] = [str(capture / Path(name).name) for name in params["output"]]
    path = Path(params["resume"])
    syncs = []
    monkeypatch.setattr(
        checkpoints,
        "sync_directory",
        lambda parent: syncs.append((parent, path.exists())),
    )

    assert execute_run(Downloader, **params, max_messages=1).success
    assert syncs == [
        (capture, False),
        (capture.parent, False),
        (tmp_path, False),
        (tmp_path, True),
    ]
    syncs.clear()
    assert execute_run(Downloader, **params).success
    assert syncs == [(tmp_path, True)]


def test_resume_rejects_oversized_sparse_checkpoint_before_parsing(params, monkeypatch):
    path = Path(params["resume"])
    with path.open("wb") as checkpoint:
        checkpoint.truncate(checkpoints._CHECKPOINT_SIZE_LIMIT + 1)

    def unexpected_parse(*args):
        pytest.fail("Oversized checkpoints should never reach the JSON parser.")

    monkeypatch.setattr(checkpoints.json, "loads", unexpected_parse)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert "checkpoint exceeds the size limit" in result.error_message


@pytest.mark.parametrize("rewrite", ["truncate", "same_length", "append"])
def test_first_checkpoint_rejects_modified_capture(params, rewrite):
    path = Path(params["resume"])

    class Modified(Downloader):
        def close(self):
            output = Path(params["output"][0])
            original = output.read_bytes()
            replacement = {
                "truncate": b"",
                "same_length": original.replace(b"Record 1", b"Replaced"),
                "append": original + original,
            }[rewrite]
            assert original != replacement
            output.write_bytes(replacement)

    result = execute_run(Modified, **{**params, "verify_output": False})
    assert not result.success
    assert "Capture files changed after checkpoint verification" in result.error_message
    assert not path.exists()


def test_checkpoint_restores_existing_identity_count_on_interrupt(params, monkeypatch):
    class Repeated(Downloader):
        records: ClassVar[list] = [message(1, 10), message(1, 10), message(2, 10)]

    observe = CaptureCheckpoint._observe

    def interrupt(checkpoint, offset, event):
        observe(checkpoint, offset, event)
        if checkpoint.events[event] == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(CaptureCheckpoint, "_observe", interrupt)
    result = execute_run(Repeated, **params)
    assert result.interrupted
    state = load_json(Path(params["resume"]))
    assert state["total"] == 1
    assert state["events"] == {checkpoints._event_identity(Repeated.records[0]): 1}

    monkeypatch.undo()
    assert execute_run(Repeated, **params).success
    assert load_json(Path(params["resume"]))["total"] == 3


def test_atomic_checkpoint_refuses_to_publish_oversized_state(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(checkpoints, "_CHECKPOINT_SIZE_LIMIT", 16)
    with pytest.raises(ValueError, match="exceeds the size limit"):
        _atomic_json(path, {"value": "a" * 16})
    assert not path.exists()
    assert not list(tmp_path.glob(".capture-*"))


def test_checkpoint_read_is_bounded_if_file_grows_after_open(params, monkeypatch):
    path = Path(params["resume"])
    path.touch()
    open_artifact = checkpoints._open_artifact
    monkeypatch.setattr(checkpoints, "_CHECKPOINT_SIZE_LIMIT", 16)

    @contextmanager
    def grow_after_open(candidate, *args, **kwargs):
        with open_artifact(candidate, *args, **kwargs) as (source, opened):
            if candidate == path:
                path.write_bytes(b"x" * 100)
            yield source, opened

    monkeypatch.setattr(checkpoints, "_open_artifact", grow_after_open)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert "checkpoint exceeds the size limit" in result.error_message


def test_resume_requires_checkpoint_destination_distinct_from_output(params):
    result = execute_run(Downloader, **{**params, "resume": params["output"][0]})
    assert not result.success
    assert "paths must be distinct" in result.error_message
    assert not Path(params["output"][0]).exists()


def test_checkpoint_format_signature_checks_custom_format_changes(tmp_path, params):
    path = tmp_path / "format.json"
    path.write_text("{}")
    kwargs = {"output": params["output"], "format_file": str(path)}
    first = CaptureCheckpoint(params["resume"], kwargs.copy())
    path.unlink()
    second = CaptureCheckpoint(params["resume"], kwargs.copy())
    assert first.fingerprint != second.fingerprint


def test_backwards_offset_during_overlap_is_rejected(params):
    class Backwards(Downloader):
        records: ClassVar[list] = [message(1, 11), message(2, 10), message(3, 12)]

    assert execute_run(Backwards, **params, max_messages=1).success
    path = Path(params["resume"])
    previous = path.read_bytes()

    result = execute_run(Backwards, **params)
    assert not result.success
    assert result.message_count == 0
    assert "offsets moved backwards" in result.error_message
    assert path.read_bytes() == previous


def test_checkpoint_hashes_actual_utf8_and_crlf_bytes(params, monkeypatch):
    class Multiline(Downloader):
        records: ClassVar[list] = [
            {**message(1, 10), "message": "Récord 1\ncontinued"},
            {**message(2, 11), "message": "Récord 2\ncontinued"},
        ]

    monkeypatch.setattr(os, "linesep", "\r\n")
    assert execute_run(Multiline, **params, max_messages=1).success
    assert execute_run(Multiline, **params).success
    for name in params["output"]:
        assert Path(name).read_bytes().endswith(b"\r\n")


def test_resumed_checkpoint_rejects_changes_to_new_records(params):
    assert execute_run(Downloader, **params, max_messages=1).success
    path = Path(params["resume"])
    previous = path.read_bytes()

    class Rewritten(Downloader):
        def close(self):
            output = Path(params["output"][0])
            original = output.read_bytes()
            replacement = original.replace(b"Record 4", b"Replaced")
            assert replacement != original
            output.write_bytes(replacement)

    result = execute_run(Rewritten, **{**params, "verify_output": False})
    assert not result.success
    assert "Capture files changed after checkpoint verification" in result.error_message
    assert path.read_bytes() == previous

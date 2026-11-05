# SPDX-License-Identifier: MIT

"""Adversarial signal delivery around real output and run accounting."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import ClassVar

import pytest

from chat_downloader.cli import _install_cli_signal_handlers
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.runtime import capture_checkpoint, runner
from chat_downloader.runtime.capture_checkpoint import CaptureCheckpoint
from chat_downloader.runtime.capture_manifest import RunManifest
from chat_downloader.runtime.runner import execute_run
from chat_downloader.utils import interrupts
from chat_downloader.utils.interrupts import (
    defer_interrupts,
    interruptible,
    raise_or_defer_interrupt,
)
from tests.test_capture_checkpoint_unit import Downloader, load_json, parameters
from tests.test_cli_signal_handlers_unit import (
    restore_signal_handlers as _restore_signal_handlers,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.usefixtures("_restore_signal_handlers")


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("checkpointed", [False, True])
@pytest.mark.parametrize("target", [".jsonl", ".txt", "observe"])
def test_signal_finishes_record_and_all_counters_before_shutdown(
    tmp_path, monkeypatch, signum, checkpointed, target
):
    if target == "observe" and not checkpointed:
        pytest.skip("Only checkpointed records have checkpoint observation.")
    params = parameters(tmp_path)
    params["run_manifest"] = str(tmp_path / "run.json")
    if not checkpointed:
        params.pop("resume")
    _install_cli_signal_handlers()
    if target == "observe":
        original_observe = CaptureCheckpoint.observe

        def observe(checkpoint, item):
            original_observe(checkpoint, item)
            if item["message_id"] == "2":
                os.kill(os.getpid(), signum)

        monkeypatch.setattr(CaptureCheckpoint, "observe", observe)
    else:
        original_write = ContinuousWriter.write

        def write(writer, item, *, flush=False):
            original_write(writer, item, flush=flush)
            if writer.file_name.endswith(target) and "Record 2" in str(item):
                os.kill(os.getpid(), signum)

        monkeypatch.setattr(ContinuousWriter, "write", write)

    result = execute_run(Downloader, **params)
    assert result.interrupted
    assert not result.success
    assert result.termination_reason == "interrupted"
    assert result.error_message == "Keyboard Interrupt"
    assert result.message_count == 2
    assert result.message_type_counts == {"text_message": 2}
    assert result.parity_status == "passed"
    report = load_json(tmp_path / "run.json")
    assert report["message_count"] == 2
    assert [writer["records_written"] for writer in report["outputs"]] == [2, 2]
    assert len((tmp_path / "chat.jsonl").read_text().splitlines()) == 2
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 2
    if checkpointed:
        assert load_json(tmp_path / "checkpoint.json")["total"] == 2
        monkeypatch.undo()
        params.pop("run_manifest")
        assert execute_run(Downloader, **params).message_count == 2


def test_provider_read_remains_immediately_interruptible(tmp_path):
    _install_cli_signal_handlers()

    class InterruptedRead(Downloader):
        records: ClassVar[list] = []

        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)

            def source():
                os.kill(os.getpid(), signal.SIGTERM)
                pytest.fail("Provider read should have been interrupted immediately.")
                yield {}

            chat.chat = source()
            return chat

    params = parameters(tmp_path)
    params.pop("resume")
    result = execute_run(InterruptedRead, **params)
    assert result.interrupted
    assert result.message_count == 0
    assert result.parity_status == "passed"
    assert not (tmp_path / "chat.jsonl").exists()


def test_nested_boundaries_defer_until_outer_record_finishes():
    actions = []
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        with defer_interrupts():
            raise_or_defer_interrupt()
            actions.append("write")
        actions.append("count")
    assert actions == ["write", "count"]
    with defer_interrupts():
        pass  # The pending interrupt must not leak into the next run.


def test_failure_clears_pending_interrupt_and_preserves_original_error():
    with pytest.raises(ValueError, match="write failed"), defer_interrupts():
        raise_or_defer_interrupt()
        with defer_interrupts():
            raise ValueError("write failed")
    with defer_interrupts(), interruptible():
        pass


def test_pending_interrupt_is_not_lost_at_normal_source_exhaustion():
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        raise_or_defer_interrupt()
        raise StopIteration
    with defer_interrupts():
        pass


def test_shutdown_boundary_observes_request_without_skipping_nested_owners():
    actions = []
    with defer_interrupts(on_interrupt=lambda: actions.append("interrupted")):
        with defer_interrupts():
            raise_or_defer_interrupt()
            actions.append("first owner")
        actions.append("second owner")
    assert actions == ["interrupted", "first owner", "second owner"]
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        raise_or_defer_interrupt()


def test_failed_shutdown_boundary_resets_interrupt_callback():
    actions = []
    with (
        pytest.raises(ValueError, match="close failed"),
        defer_interrupts(on_interrupt=lambda: actions.append("interrupted")),
    ):
        raise_or_defer_interrupt()
        raise ValueError("close failed")
    assert actions == ["interrupted"]
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        raise_or_defer_interrupt()
    assert actions == ["interrupted"]


@pytest.mark.parametrize("delivery", ["before_install", "after_install"])
def test_shutdown_boundary_handles_signal_during_observer_installation(
    monkeypatch, delivery
):
    actions = []
    callbacks = interrupts._ON_INTERRUPT

    class SignalingCallbackInstallation:
        def get(self):
            return callbacks.get()

        def set(self, callback):
            if delivery == "before_install":
                os.kill(os.getpid(), signal.SIGTERM)
            token = callbacks.set(callback)
            if delivery == "after_install":
                os.kill(os.getpid(), signal.SIGTERM)
            return token

        def reset(self, token):
            callbacks.reset(token)

    monkeypatch.setattr(interrupts, "_ON_INTERRUPT", SignalingCallbackInstallation())
    _install_cli_signal_handlers()
    with defer_interrupts(on_interrupt=lambda: actions.append("interrupted")):
        actions.append("protected work")
    assert actions == ["interrupted", "protected work"]
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        raise_or_defer_interrupt()
    assert actions == ["interrupted", "protected work"]


def test_second_signal_can_interrupt_an_incomplete_record():
    _install_cli_signal_handlers()
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)
        pytest.fail("Second signal should interrupt immediately.")
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    with defer_interrupts():
        pass


@pytest.mark.parametrize("propagate", [False, True])
def test_first_signal_during_chat_close_finishes_all_owner_release(propagate):
    owners = []

    class ClosingChat:
        def __iter__(self):
            return iter(())

        def close(self):
            owners.append("chat started")
            os.kill(os.getpid(), signal.SIGTERM)
            owners.append("chat released")

    class ClosingDownloader:
        def get_chat(self, **_kwargs):
            return ClosingChat()

        def close(self):
            owners.append("downloader released")

    _install_cli_signal_handlers()
    if propagate:
        with pytest.raises(KeyboardInterrupt):
            execute_run(ClosingDownloader, quiet=True, propagate_interrupt=True)
    else:
        result = execute_run(ClosingDownloader, quiet=True)
        assert not result.success
        assert result.interrupted
        assert result.error_message == "Keyboard Interrupt"
        assert result.termination_reason == "interrupted"
        assert result.failure_stage == "shutdown"
    assert owners == ["chat started", "chat released", "downloader released"]


@pytest.mark.parametrize(
    "boundary",
    [
        "chat_close",
        "interrupt_observer",
        "downloader_close",
        "inspection",
        "verification",
        "checkpoint_save",
        "checkpoint_hash",
        "manifest_hash",
        "manifest_encode",
        "manifest_create",
        "manifest_write",
        "manifest_flush",
    ],
)
@pytest.mark.parametrize("propagate", [False, True])
def test_first_shutdown_signal_preserves_resume_and_reports_interruption(
    tmp_path, monkeypatch, boundary, propagate
):
    params = parameters(tmp_path)
    assert execute_run(Downloader, **params, max_messages=1).success
    report_path = tmp_path / "run.json"
    owners = []
    signaled = False
    saving_checkpoint = False
    writing_manifest = False

    def interrupt():
        nonlocal signaled
        if not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGTERM)

    class ObservedDownloader(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            original_close = chat.close
            close_calls = 0

            def close():
                nonlocal close_calls
                close_calls += 1
                # Chat also closes itself on source exhaustion; deliver during
                # the runner's owner-release pass, not that record boundary.
                if boundary == "chat_close" and close_calls == 2:
                    interrupt()
                original_close()
                owners.append("chat")

            chat.close = close
            return chat

        def close(self):
            if boundary == "downloader_close":
                interrupt()
            super().close()
            owners.append("downloader")

    if boundary in {"inspection", "verification"}:
        name = (
            "inspect_provider_capture" if boundary == "inspection" else "verify_capture"
        )
        original = getattr(runner, name)

        def verify(*args, **kwargs):
            interrupt()
            return original(*args, **kwargs)

        monkeypatch.setattr(runner, name, verify)

    original_save = CaptureCheckpoint.save

    def save(checkpoint, chat, count):
        nonlocal saving_checkpoint
        saving_checkpoint = True
        try:
            if boundary == "checkpoint_save":
                interrupt()
            return original_save(checkpoint, chat, count)
        finally:
            saving_checkpoint = False

    monkeypatch.setattr(CaptureCheckpoint, "save", save)

    original_signature = capture_checkpoint._file_signature

    def signature(path):
        if boundary == "checkpoint_hash" and saving_checkpoint:
            interrupt()
        return original_signature(path)

    monkeypatch.setattr(capture_checkpoint, "_file_signature", signature)
    original_manifest = RunManifest.write

    def manifest(manifest, *args, **kwargs):
        nonlocal writing_manifest
        writing_manifest = True
        try:
            return original_manifest(manifest, *args, **kwargs)
        finally:
            writing_manifest = False

    monkeypatch.setattr(RunManifest, "write", manifest)
    original_digest = capture_checkpoint.hashlib.file_digest

    def digest(*args, **kwargs):
        value = original_digest(*args, **kwargs)
        if boundary == "manifest_hash" and writing_manifest:
            interrupt()
        return value

    monkeypatch.setattr(capture_checkpoint.hashlib, "file_digest", digest)
    original_dumps = json.dumps

    def dumps(*args, **kwargs):
        value = original_dumps(*args, **kwargs)
        if boundary == "manifest_encode" and writing_manifest:
            interrupt()
        return value

    monkeypatch.setattr(json, "dumps", dumps)
    original_open = Path.open

    class InterruptedReport:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, value):
            written = self.stream.write(value)
            if boundary == "manifest_write":
                interrupt()
            return written

        def flush(self):
            self.stream.flush()
            if boundary == "manifest_flush":
                interrupt()

    def open_report(path, mode="r", *args, **kwargs):
        if path == report_path and mode == "x":
            if boundary == "manifest_create":
                interrupt()
            stream = original_open(path, mode, *args, **kwargs)
            if boundary in {"manifest_write", "manifest_flush"}:
                return InterruptedReport(stream)
            return stream
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_report)
    if boundary == "interrupt_observer":
        callbacks = interrupts._ON_INTERRUPT

        class SignalingCallbackInstallation:
            def get(self):
                return callbacks.get()

            def set(self, callback):
                interrupt()
                return callbacks.set(callback)

            def reset(self, token):
                callbacks.reset(token)

        monkeypatch.setattr(
            interrupts, "_ON_INTERRUPT", SignalingCallbackInstallation()
        )
    expected_stage = (
        "manifest"
        if boundary.startswith("manifest")
        else "shutdown"
        if boundary in {"chat_close", "downloader_close", "interrupt_observer"}
        else "verification"
    )
    _install_cli_signal_handlers()
    kwargs = {
        **params,
        "max_messages": 2,
        "run_manifest": str(report_path),
        "propagate_interrupt": propagate,
    }
    if propagate:
        with pytest.raises(KeyboardInterrupt):
            execute_run(ObservedDownloader, **kwargs)
    else:
        result = execute_run(ObservedDownloader, **kwargs)
        assert result.interrupted
        assert not result.success
        assert result.termination_reason == "interrupted"
        assert result.error_message == "Keyboard Interrupt"
        assert result.message_count == 2
        assert result.message_type_counts == {"text_message": 2}
        assert result.parity_status == "passed"
        assert result.failure_stage == expected_stage
    assert signaled
    assert owners[-1] == "downloader"
    assert owners.count("chat") >= 2
    assert not (tmp_path / "checkpoint.json.lock").exists()
    assert load_json(tmp_path / "checkpoint.json")["total"] == 3
    report = load_json(report_path)
    assert report["success"] is False
    assert report["replay_complete"] is False
    assert report["termination_reason"] == "interrupted"
    assert report["failure_stage"] == expected_stage
    assert report["message_count"] == 2
    assert report["parity_status"] == "passed"
    assert [output["records_written"] for output in report["outputs"]] == [2, 2]
    resumed = execute_run(Downloader, **params)
    assert resumed.success
    assert resumed.message_count == 1
    rows = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [row["message_id"] for row in rows] == ["1", "2", "3", "4"]
    assert len((tmp_path / "chat.txt").read_text().splitlines()) == 4


def test_first_shutdown_signal_preserves_original_capture_error(tmp_path):
    class FailedCapture(Downloader):
        records = Downloader.records[:2]
        failure = ValueError("original capture failure")

        def close(self):
            os.kill(os.getpid(), signal.SIGTERM)

    _install_cli_signal_handlers()
    params = parameters(tmp_path)
    result = execute_run(
        FailedCapture, **params, run_manifest=str(tmp_path / "run.json")
    )
    assert result.interrupted
    assert not result.success
    assert result.error_message == "original capture failure"
    assert result.failure_stage == "capture"
    assert result.termination_reason == "error"
    assert load_json(tmp_path / "checkpoint.json")["total"] == 2
    report = load_json(tmp_path / "run.json")
    assert report["success"] is False
    assert report["termination_reason"] == "error"
    assert report["failure_stage"] == "capture"


def test_save_failure_keeps_priority_over_deferred_shutdown_signal(
    tmp_path, monkeypatch
):
    def save(*_args):
        os.kill(os.getpid(), signal.SIGTERM)
        raise OSError("checkpoint save failed")

    monkeypatch.setattr(CaptureCheckpoint, "save", save)
    _install_cli_signal_handlers()
    result = execute_run(Downloader, **parameters(tmp_path))
    assert result.interrupted
    assert not result.success
    assert result.error_message == "checkpoint save failed"
    assert result.termination_reason == "error"
    assert result.failure_stage == "verification"
    assert not (tmp_path / "checkpoint.json").exists()
    assert not (tmp_path / "checkpoint.json.lock").exists()


def test_manifest_failure_keeps_priority_over_deferred_shutdown_signal(
    tmp_path, monkeypatch
):
    def write(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        raise OSError("manifest write failed")

    monkeypatch.setattr(RunManifest, "write", write)
    _install_cli_signal_handlers()
    result = execute_run(
        Downloader,
        **parameters(tmp_path),
        run_manifest=str(tmp_path / "run.json"),
    )
    assert result.interrupted
    assert not result.success
    assert result.error_message == "Unable to write run manifest: manifest write failed"
    assert result.termination_reason == "error"
    assert result.failure_stage == "manifest"
    assert load_json(tmp_path / "checkpoint.json")["total"] == 4
    assert not (tmp_path / "run.json").exists()
    assert not (tmp_path / "checkpoint.json.lock").exists()


@pytest.mark.parametrize(
    "failure_at", ["inspection", "verification", "save", "manifest"]
)
@pytest.mark.parametrize("primary_failure", [False, True])
def test_substantive_error_replaces_only_interruption_failure_stage(
    tmp_path, monkeypatch, failure_at, primary_failure
):
    class InterruptedShutdown(Downloader):
        records = Downloader.records[:2]
        failure = ValueError("original capture failure") if primary_failure else None

        def close(self):
            os.kill(os.getpid(), signal.SIGTERM)

    def fail(*_args, **_kwargs):
        raise OSError("substantive failure")

    if failure_at == "inspection":
        monkeypatch.setattr(runner, "inspect_provider_capture", fail)
    elif failure_at == "verification":
        monkeypatch.setattr(runner, "verify_capture", fail)
    elif failure_at == "save":
        monkeypatch.setattr(CaptureCheckpoint, "save", fail)
    else:
        monkeypatch.setattr(RunManifest, "write", fail)
    _install_cli_signal_handlers()
    report_path = tmp_path / "run.json"
    result = execute_run(
        InterruptedShutdown,
        **parameters(tmp_path),
        run_manifest=str(report_path),
    )
    expected_stage = (
        "capture"
        if primary_failure
        else "manifest"
        if failure_at == "manifest"
        else "verification"
    )
    expected_error = (
        "original capture failure"
        if primary_failure
        else "Unable to write run manifest: substantive failure"
        if failure_at == "manifest"
        else "substantive failure"
    )
    assert result.interrupted
    assert not result.success
    assert result.error_message == expected_error
    assert result.failure_stage == expected_stage
    assert result.termination_reason == "error"
    assert not (tmp_path / "checkpoint.json.lock").exists()
    if failure_at == "manifest":
        assert not report_path.exists()
    else:
        report = load_json(report_path)
        assert report["success"] is False
        assert report["termination_reason"] == "error"
        assert report["failure_stage"] == expected_stage


def test_second_signal_interrupts_shutdown_without_closing_later_owners(tmp_path):
    owners = []

    class ForcedShutdown(Downloader):
        def close(self):
            os.kill(os.getpid(), signal.SIGTERM)
            owners.append("first signal returned")
            os.kill(os.getpid(), signal.SIGTERM)
            owners.append("second signal returned")

    _install_cli_signal_handlers()
    with pytest.raises(KeyboardInterrupt):
        execute_run(
            ForcedShutdown,
            **parameters(tmp_path),
            run_manifest=str(tmp_path / "run.json"),
        )
    assert owners == ["first signal returned"]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert not (tmp_path / "checkpoint.json.lock").exists()
    assert not (tmp_path / "checkpoint.json").exists()
    assert not (tmp_path / "run.json").exists()
    with defer_interrupts():
        pass

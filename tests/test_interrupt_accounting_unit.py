# SPDX-License-Identifier: MIT

"""Adversarial signal delivery around real output and run accounting."""

from __future__ import annotations

import os
import signal
from typing import ClassVar

import pytest

from chat_downloader.cli import _install_cli_signal_handlers
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.runtime.capture_checkpoint import CaptureCheckpoint
from chat_downloader.runtime.runner import execute_run
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


def test_second_signal_can_interrupt_an_incomplete_record():
    _install_cli_signal_handlers()
    with pytest.raises(KeyboardInterrupt), defer_interrupts():
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGTERM)
        pytest.fail("Second signal should interrupt immediately.")
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    with defer_interrupts():
        pass

# SPDX-License-Identifier: MIT

"""Closed stdout pipes must preserve cleanup and the CLI's failure status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest

from chat_downloader.cli import main
from chat_downloader.runtime.runner import RunResult
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat
from tests.core_third_helpers import restore_loggers


def _run_capture(directory: str) -> None:
    """Exercise real dispatch and writers with an offline, coordinated source."""
    root = Path(directory)

    class FakeSite(BaseChatDownloader):
        _NAME = "fake.test"
        _VALID_URLS: ClassVar[dict[str, str]] = {
            "_get_chat_by_fake": r"https://fake\.test/(?P<id>\w+)"
        }

        def _get_chat_by_fake(self, match, request):
            def messages():
                try:
                    yield {"message_id": "first", "message": "first"}
                    sys.stdin.readline()
                    yield {"message_id": "second", "message": "second"}
                finally:
                    (root / "source-closed").touch()

            return Chat(messages(), id=match.group("id"), status="live")

        def close(self):
            super().close()
            (root / "session-closed").touch()

    with patch(
        "chat_downloader.runtime.site_dispatch.get_all_sites",
        return_value=[FakeSite],
    ):
        main(
            [
                "https://fake.test/stream",
                "--output",
                str(root / "chat.jsonl"),
                "--output",
                str(root / "chat.txt"),
                "--run_manifest",
                str(root / "run.json"),
                "--verify_output",
            ]
        )


@pytest.mark.parametrize("read_first", [False, True])
def test_closed_pipe_preserves_subprocess_status_and_capture_cleanup(
    tmp_path, read_first
):
    script = (
        "import sys; "
        "from tests.test_cli_broken_pipe_unit import _run_capture; "
        "_run_capture(sys.argv[1])"
    )
    with subprocess.Popen(  # noqa: S603 - fixed interpreter and offline helper
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        if read_first:
            assert process.stdout.readline() == ": first\n"
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(input="\n", timeout=10)

    assert process.returncode == 1, stderr
    assert "Exception ignored" not in stderr
    assert "BrokenPipeError" not in stderr
    assert "Traceback" not in stderr
    assert (tmp_path / "source-closed").exists()
    assert (tmp_path / "session-closed").exists()
    expected = ["first", "second"] if read_first else ["first"]
    records = [
        json.loads(line) for line in (tmp_path / "chat.jsonl").read_text().splitlines()
    ]
    assert [record["message_id"] for record in records] == expected
    assert (tmp_path / "chat.txt").read_text().splitlines() == [
        f": {message}" for message in expected
    ]
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["success"] is False
    assert manifest["termination_reason"] == "error"
    assert manifest["parity_status"] == "passed"
    assert manifest["failure_stage"] == "capture"
    assert manifest["message_count"] == len(expected)
    assert all(
        output["records_written"] == len(expected) for output in manifest["outputs"]
    )


@pytest.mark.parametrize("arguments", [["--help"], ["--version"]])
@pytest.mark.parametrize("unbuffered", ["0", "1"])
def test_closed_pipe_during_argument_output_exits_without_shutdown_error(
    arguments, unbuffered
):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(  # noqa: S603 - fixed local module and CLI arguments
            [sys.executable, "-m", "chat_downloader", *arguments],
            env=dict(os.environ, PYTHONUNBUFFERED=unbuffered),
            stdout=write_fd,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 1
    assert result.stderr == ""


def test_buffered_pipe_failure_redirects_final_flush_and_exits_one():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    def buffered_run(**kwargs):
        sys.stdout.write("buffered output")
        return RunResult(success=True)

    with (
        os.fdopen(write_fd, "w", encoding="utf-8") as stdout,
        redirect_stdout(stdout),
        restore_loggers(),
        patch("chat_downloader.cli.run", side_effect=buffered_run),
        pytest.raises(SystemExit) as error,
    ):
        main(["https://fake.test/stream"])
    assert error.value.code == 1

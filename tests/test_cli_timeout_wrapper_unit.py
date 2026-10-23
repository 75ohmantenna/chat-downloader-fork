"""Supported timeout launch forms must preserve replay checkpoints offline."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest

from chat_downloader.cli import main
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.models import Chat


def _run_timeout_capture(directory: str) -> None:
    """Block after a committed record while exercising the actual CLI cleanup."""
    root = Path(directory)

    class FakeSite(BaseChatDownloader):
        _NAME = "fake.test"
        _VALID_URLS: ClassVar[dict[str, str]] = {
            "_get_chat": r"https://fake\.test/(?P<id>\w+)"
        }

        def _get_chat(self, match, request):
            def source():
                try:
                    yield {
                        "message_id": "first",
                        "message_type": "text_message",
                        "message": "first",
                        "time_in_seconds": 1,
                    }
                    (root / "ready").touch()
                    threading.Event().wait()
                finally:
                    (root / "source-closed").touch()

            return Chat(source(), id=match.group("id"), status="completed")

        def close(self):
            super().close()
            (root / "session-closed").touch()

    with patch(
        "chat_downloader.runtime.site_dispatch.get_all_sites", return_value=[FakeSite]
    ):
        main(
            [
                "https://fake.test/replay",
                "--quiet",
                "--output",
                str(root / "chat.jsonl"),
                "--output",
                str(root / "chat.txt"),
                "--resume",
                str(root / "checkpoint.json"),
                "--verify_output",
                "--run_manifest",
                str(root / "manifest.json"),
            ]
        )


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("timeout") or not shutil.which("uv"),
    reason="GNU timeout and uv integration requires Linux launch tools",
)
@pytest.mark.parametrize("launcher", ["direct", "uv-foreground"])
def test_timeout_launchers_finalize_checkpoint_without_duplicate_signals(
    tmp_path, launcher
):
    timeout = shutil.which("timeout")
    uv = shutil.which("uv")
    script = (
        "import sys; from tests.test_cli_timeout_wrapper_unit "
        "import _run_timeout_capture; _run_timeout_capture(sys.argv[1])"
    )
    command = [timeout]
    if launcher == "uv-foreground":
        command += ["--foreground", "4s", uv, "run", "--locked", "--no-sync"]
    else:
        command += ["4s"]
    command += [sys.executable, "-c", script, str(tmp_path)]
    result = subprocess.run(  # noqa: S603 - resolved local tools and fixed offline feed
        command, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 124, result.stderr
    assert (tmp_path / "ready").exists(), result.stderr
    assert (tmp_path / "source-closed").exists()
    assert (tmp_path / "session-closed").exists()
    assert "Second signal" not in result.stderr
    assert "Traceback" not in result.stderr
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["total"] == 1
    report = json.loads((tmp_path / "manifest.json").read_text())
    assert report["termination_reason"] == "interrupted"
    assert report["parity_status"] == "passed"
    assert report["message_count"] == 1

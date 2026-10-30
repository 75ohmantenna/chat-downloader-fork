# SPDX-License-Identifier: MIT

"""Smoke orchestration fails closed and never prints child text or proxies."""

from __future__ import annotations

import json
import stat
import subprocess
from types import SimpleNamespace

import pytest

from scripts import smoke_kick_live as smoke


def _manifest(directory, **changes):
    data = {
        "message_count": 2,
        "success": True,
        "parity_status": "passed",
        "provider_inspection": {"status": "ok"},
    } | changes
    (directory / "manifest.json").write_text(json.dumps(data))


def test_matrix_uses_bounded_children_private_artifacts_and_content_free_report(
    tmp_path, monkeypatch, capsys
):
    calls = []

    def child(command, **kwargs):
        calls.append((command, kwargs))
        directory = kwargs["cwd"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert 0 < kwargs["timeout"] <= 46
        if directory.name != "quiet":
            kwargs["stdout"].write(b"PRIVATE_CHAT_TEXT")
        kwargs["stderr"].write(b"PRIVATE_PROVIDER_ERROR")
        invalid = directory.name in {
            "invalid_extension",
            "live_resume",
            "require_complete",
        }
        if invalid:
            diagnostic = {
                "invalid_extension": "Unsupported output format 'csv'.",
                "live_resume": (
                    "Resume requires a completed replay with stable identity."
                ),
                "require_complete": "Complete capture requires a completed replay.",
            }[directory.name]
            kwargs["stderr"].write(diagnostic.encode())
        if not invalid and directory.name not in {"help", "version"}:
            _manifest(directory)
        return SimpleNamespace(returncode=1 if invalid else 0)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    directory = tmp_path / "artifacts"
    assert (
        smoke.main(
            [
                "--run-network",
                "--duration",
                "1",
                "--case-duration",
                "1",
                "--proxy",
                "socks5://PRIVATE_PROXY:secret@host:1080",
                "--artifacts",
                str(directory),
            ]
        )
        == 0
    )
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    report = json.loads((directory / "report.json").read_text())
    assert report["passed"] is True
    assert len(calls) == 12
    proxy_calls = [cmd for cmd, _ in calls if "--proxy" in cmd]
    assert len(proxy_calls) == 1
    assert "PRIVATE" not in capsys.readouterr().out
    assert "PRIVATE" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        smoke.main(["--run-network", "--artifacts", str(directory)])


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "86401", "bad"])
def test_matrix_rejects_unbounded_duration_before_creating_artifacts(tmp_path, value):
    directory = tmp_path / "artifacts"
    with pytest.raises(SystemExit) as exc:
        smoke.main(
            ["--run-network", "--duration", value, "--artifacts", str(directory)]
        )
    assert exc.value.code == 2
    assert not directory.exists()


@pytest.mark.parametrize("options", [[], ["--run-network", "--channel", "../PRIVATE"]])
def test_network_opt_in_and_channel_validation_are_required(tmp_path, options):
    directory = tmp_path / "artifacts"
    with pytest.raises(SystemExit) as exc:
        smoke.main([*options, "--artifacts", str(directory)])
    assert exc.value.code == 2
    assert not directory.exists()


@pytest.mark.parametrize(
    "failure",
    ["timeout", "missing", "invalid", "shape", "exit", "empty", "review", "quiet"],
)
def test_child_failures_never_pass_or_echo_child_text(tmp_path, monkeypatch, failure):
    def child(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"PRIVATE")
        if failure != "missing":
            _manifest(tmp_path, **({"message_count": 0} if failure == "empty" else {}))
        if failure in {"invalid", "shape"}:
            (tmp_path / "manifest.json").write_text(
                "PRIVATE" if failure == "invalid" else "[]"
            )
        if failure == "review":
            _manifest(tmp_path, provider_inspection={"status": "review"})
        if failure == "quiet":
            kwargs["stdout"].write(b"PRIVATE")
        return SimpleNamespace(returncode=1 if failure == "exit" else 0)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    result = smoke._run_case(
        tmp_path,
        "quiet" if failure == "quiet" else "capture",
        [],
        expected_success=True,
        timeout=1,
    )
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"message_count": True},
        {"message_count": -1},
        {"message_count": "PRIVATE"},
        {"parity_status": "PRIVATE"},
        {"parity_status": []},
        {"provider_inspection": {"status": "PRIVATE"}},
    ],
)
def test_manifest_fields_cannot_inject_content_into_aggregate_report(
    tmp_path, monkeypatch, changes
):
    _manifest(tmp_path, **changes)
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "capture", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_generic_network_failure_does_not_pass_invalid_argument_case(
    tmp_path, monkeypatch
):
    def child(*args, **kwargs):
        kwargs["stderr"].write(b"Network error: PRIVATE")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    result = smoke._run_case(
        tmp_path,
        "invalid_extension",
        [],
        expected_success=False,
        timeout=1,
    )
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)

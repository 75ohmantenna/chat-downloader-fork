# SPDX-License-Identifier: MIT

"""Smoke orchestration fails closed and never prints child text or proxies."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from chat_downloader.metadata import __program__, __version__
from chat_downloader.sites.kick.capture_inspection import inspect_capture
from chat_downloader.sites.kick.live_service import _KickLiveDiagnostics
from chat_downloader.utils import _bounded_records, atomic_files
from scripts import smoke_kick_live as smoke


def _manifest(
    directory,
    *,
    diagnostics=None,
    prefetched=0,
    message_type="text_message",
    sorted_keys=True,
    **changes,
):
    state = _KickLiveDiagnostics().summary
    state.update(
        websocket_frame_count=3,
        control_frame_count=1,
        parsed_event_count=2,
        pusher_connection_count=1,
        public_subscription_count=1,
        live_emitted_count=2,
    )
    state.update(diagnostics or {})
    count = (
        sum(
            state[key]
            for key in (
                "preloaded_emitted_count",
                "startup_backfill_emitted_count",
                "live_emitted_count",
                "reconnect_backfill_emitted_count",
            )
        )
        - prefetched
    )
    data = {
        "message_count": count,
        "message_type_counts": {message_type: count} if count else {},
        "success": True,
        "parity_status": "passed",
        "termination_reason": "timeout",
        "provider_diagnostics": state,
        "prefetched_after_deadline_count": prefetched,
        "deadline_prefetch_count_complete": True,
    }
    capture = directory / "chat.jsonl"
    capture.write_text(
        "".join(
            json.dumps(
                {
                    "message_type": message_type,
                    "message_id": str(index),
                    "timestamp": index,
                    "author": {"id": "author"},
                    "message": "PRIVATE_CHAT_TEXT",
                },
                sort_keys=sorted_keys,
            )
            + "\n"
            for index in range(count)
        )
    )
    data["provider_inspection"] = inspect_capture(capture, run_summary=data)
    data.update(changes)
    (directory / "manifest.json").write_text(json.dumps(data))
    return data


@pytest.mark.parametrize("channel_options", [[], ["--channel", "chosen"]])
def test_matrix_uses_bounded_children_private_artifacts_and_content_free_report(
    tmp_path, monkeypatch, capsys, channel_options
):
    calls = []
    monkeypatch.setenv("KICK_TEST_CHANNEL", "selected")
    channel = "chosen" if channel_options else "selected"

    def child(command, **kwargs):
        calls.append((command, kwargs))
        assert command[3] == f"https://kick.com/{channel}"
        directory = kwargs["cwd"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        if directory.name == "inactivity":
            assert (
                float(command[command.index("--timeout") + 1])
                > float(command[command.index("--inactivity_timeout") + 1])
                == 5
            )
            assert kwargs["timeout"] == 51
        else:
            assert 0 < kwargs["timeout"] <= 46
        if directory.name == "help":
            kwargs["stdout"].write(
                f"usage: {__program__} --help --version --message_types "
                "--inactivity_timeout\n".encode()
            )
        elif directory.name == "version":
            kwargs["stdout"].write(f"{__version__}\n".encode())
        elif directory.name != "quiet":
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
        if directory.name == "inactivity":
            _manifest(
                directory,
                message_type="goal_canceled",
                termination_reason="inactivity_timeout",
            )
        elif not invalid and directory.name not in {"help", "version"}:
            _manifest(directory)
        return SimpleNamespace(returncode=1 if invalid else 0)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    directory = tmp_path / "artifacts"
    assert (
        smoke.main(
            [
                "--run-network",
                *channel_options,
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
    assert len(calls) == 13
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


@pytest.mark.parametrize(
    ("options", "environment", "reason"),
    [
        ([], "selected", "required: --run-network"),
        (["--run-network"], None, "with --channel or KICK_TEST_CHANNEL"),
        (["--run-network"], "", "with --channel or KICK_TEST_CHANNEL"),
        (["--run-network"], "../PRIVATE", "Use a Kick channel slug."),
        (
            ["--run-network", "--channel", "../PRIVATE"],
            "selected",
            "Use a Kick channel slug.",
        ),
    ],
)
def test_network_opt_in_and_channel_validation_are_required(
    tmp_path, monkeypatch, capsys, options, environment, reason
):
    if environment is None:
        monkeypatch.delenv("KICK_TEST_CHANNEL", raising=False)
    else:
        monkeypatch.setenv("KICK_TEST_CHANNEL", environment)
    directory = tmp_path / "artifacts"
    with pytest.raises(SystemExit) as exc:
        smoke.main([*options, "--artifacts", str(directory)])
    assert exc.value.code == 2
    assert not directory.exists()
    captured = capsys.readouterr()
    assert reason in captured.err
    assert "PRIVATE" not in captured.err


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


@pytest.mark.parametrize(
    "diagnostics",
    [
        pytest.param(
            {
                "websocket_frame_count": 1,
                "parsed_event_count": 0,
                "preloaded_emitted_count": 1,
                "startup_backfill_emitted_count": 1,
                "reconnect_backfill_emitted_count": 1,
                "live_emitted_count": 0,
            },
            id="preload-and-history-only",
        ),
        pytest.param(
            {
                "websocket_frame_count": 2,
                "control_frame_count": 2,
                "parsed_event_count": 0,
                "live_emitted_count": 0,
            },
            id="control-only",
        ),
        pytest.param(
            {
                "websocket_frame_count": 1,
                "synthetic_frame_count": 2,
            },
            id="public-rest-only",
        ),
        pytest.param(
            {
                "websocket_frame_count": 2,
                "control_frame_count": 0,
                "public_subscription_count": 0,
            },
            id="publication-without-confirmation",
        ),
        pytest.param(
            {
                "websocket_frame_count": 0,
                "control_frame_count": 0,
                "parsed_event_count": 0,
                "pusher_connection_count": 0,
                "public_subscription_count": 0,
                "live_emitted_count": 0,
                "preloaded_emitted_count": 2,
            },
            id="no-subscription",
        ),
    ],
)
def test_realtime_case_rejects_otherwise_clean_non_delivery_captures(
    tmp_path, monkeypatch, diagnostics
):
    manifest = _manifest(tmp_path, diagnostics=diagnostics)
    assert manifest["provider_inspection"]["status"] == "ok"
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert result["realtime_delivery"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_realtime_case_requires_a_recorded_socket_application_publication(
    tmp_path, monkeypatch
):
    _manifest(
        tmp_path,
        diagnostics={
            "preloaded_emitted_count": 2,
            "startup_backfill_emitted_count": 1,
            "reconnect_backfill_emitted_count": 1,
            "synthetic_frame_count": 1,
            "parsed_event_count": 3,
            "live_emitted_count": 3,
        },
        prefetched=1,
    )
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is True
    assert result["socket_records_min"] == 1
    assert result["confirmed_subscriptions"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_socket_publication_discarded_at_deadline_is_not_recorded_delivery(
    tmp_path, monkeypatch
):
    _manifest(
        tmp_path,
        diagnostics={
            "parsed_event_count": 2,
            "synthetic_frame_count": 1,
            "websocket_frame_count": 2,
        },
        prefetched=1,
    )
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert result["socket_records_min"] == 0


@pytest.mark.parametrize("case", ["quiet", "inactivity"])
def test_ordinary_quiet_capture_can_succeed_without_records(
    tmp_path, monkeypatch, case
):
    _manifest(
        tmp_path,
        diagnostics={
            "websocket_frame_count": 1,
            "parsed_event_count": 0,
            "live_emitted_count": 0,
        },
        termination_reason="inactivity_timeout" if case == "inactivity" else "timeout",
    )
    (tmp_path / "chat.jsonl").unlink()
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, case, [], expected_success=True, timeout=1)
    assert result["passed"] is True
    assert result["records"] == 0


@pytest.mark.parametrize("count", [0, 2])
def test_inactivity_rejects_capture_files_inconsistent_with_manifest(
    tmp_path, monkeypatch, count
):
    _manifest(
        tmp_path,
        diagnostics={
            "websocket_frame_count": count + 1,
            "parsed_event_count": count,
            "live_emitted_count": count,
        },
        message_type="goal_canceled",
        termination_reason="inactivity_timeout",
    )
    capture = tmp_path / "chat.jsonl"
    if count:
        capture.unlink()
    else:
        capture.write_text('{"message_type": "goal_canceled"}\n')
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(
        tmp_path, "inactivity", [], expected_success=True, timeout=1
    )
    assert result["passed"] is False


@pytest.mark.parametrize("case", ["filtered", "inactivity"])
def test_filter_rejects_manifest_type_counts_that_disagree_with_capture(
    tmp_path, monkeypatch, case
):
    message_type = "text_message" if case == "filtered" else "goal_canceled"
    _manifest(
        tmp_path,
        message_type=message_type,
        message_type_counts={message_type: 1, "PRIVATE": 1},
        termination_reason="inactivity_timeout" if case == "inactivity" else "timeout",
    )
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, case, [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"message_type":"PRIVATE","message_type":"text_message"}\n',
        b'["PRIVATE"]\n',
        b'{"PRIVATE":NaN,"message_type":"text_message"}\n',
        None,
    ],
)
def test_filtered_capture_rejects_invalid_jsonl_with_content_free_error(
    tmp_path, monkeypatch, payload
):
    _manifest(tmp_path)
    capture = tmp_path / "chat.jsonl"
    if payload is None:
        capture.unlink()
        capture.mkdir()
    else:
        capture.write_bytes(payload * 2)
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "filtered", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert result["error"] == "invalid_or_missing_capture"
    assert "PRIVATE" not in json.dumps(result)


def test_filtered_capture_applies_physical_record_limit(tmp_path, monkeypatch):
    _manifest(tmp_path)
    monkeypatch.setattr(_bounded_records, "MAX_CAPTURE_RECORD_BYTES", 16)
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "filtered", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert result["error"] == "invalid_or_missing_capture"


def test_recursive_manifest_fails_with_content_free_error(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("[" * 4096 + '"PRIVATE"' + "]" * 4096)
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "filtered", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert result["error"] == "invalid_or_missing_manifest"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    ("count", "reason", "passed"),
    [
        (3, "timeout", True),
        (5, "message_limit", True),
        (6, "message_limit", False),
        (3, "message_limit", False),
        (5, "timeout", True),
        (0, "completed", False),
    ],
)
def test_quiet_requires_message_cap_or_early_deadline(
    tmp_path, monkeypatch, count, reason, passed
):
    _manifest(
        tmp_path,
        diagnostics={
            "websocket_frame_count": count + 1,
            "parsed_event_count": count,
            "live_emitted_count": count,
        },
        termination_reason=reason,
    )
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "quiet", [], expected_success=True, timeout=1)
    assert result["passed"] is passed


@pytest.mark.parametrize(
    ("case", "message_type", "reason", "sorted_keys", "passed"),
    [
        ("inactivity", "goal_canceled", "inactivity_timeout", False, True),
        ("inactivity", "goal_canceled", "timeout", True, False),
        ("inactivity", "text_message", "inactivity_timeout", True, False),
        ("filtered", "text_message", "timeout", True, True),
        ("filtered", "text_message", "timeout", False, False),
        ("filtered", "goal_canceled", "timeout", True, False),
    ],
)
def test_filtered_capture_cases_enforce_their_cli_options(
    tmp_path, monkeypatch, case, message_type, reason, sorted_keys, passed
):
    manifest = _manifest(
        tmp_path,
        message_type=message_type,
        termination_reason=reason,
        sorted_keys=sorted_keys,
    )
    assert manifest["provider_inspection"]["status"] == "ok"
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, case, [], expected_success=True, timeout=1)
    assert result["passed"] is passed
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("case", ["help", "version"])
def test_cli_information_matches_real_offline_output(tmp_path, case):
    result = smoke._run_case(
        tmp_path, case, [f"--{case}"], expected_success=True, timeout=10
    )
    assert result["passed"] is True


@pytest.mark.parametrize("case", ["help", "version"])
def test_cli_information_rejects_unrelated_stdout(tmp_path, monkeypatch, case):
    def child(*args, **kwargs):
        kwargs["stdout"].write(b"PRIVATE_CHAT_TEXT")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    result = smoke._run_case(tmp_path, case, [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_help_rejects_renamed_option_with_expected_prefix(tmp_path, monkeypatch):
    def child(*args, **kwargs):
        kwargs["stdout"].write(
            f"usage: {__program__} --help --version --message_types_renamed "
            "--inactivity_timeout\n".encode()
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke.subprocess, "run", child)
    result = smoke._run_case(tmp_path, "help", [], expected_success=True, timeout=1)
    assert result["passed"] is False


@pytest.mark.parametrize(
    "key",
    [
        "websocket_frame_count",
        "control_frame_count",
        "parsed_event_count",
        "pusher_connection_count",
        "centrifugo_connection_count",
        "public_subscription_count",
        "synthetic_frame_count",
        "live_emitted_count",
    ],
)
@pytest.mark.parametrize("value", [None, True, -1, 1.5, "PRIVATE", [], {}])
def test_realtime_liveness_counters_fail_closed(tmp_path, monkeypatch, key, value):
    manifest = _manifest(tmp_path)
    manifest["provider_diagnostics"][key] = value
    manifest["provider_inspection"]["frame_accounting"][key] = value
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    "missing",
    [
        "provider_diagnostics",
        "provider_inspection.frame_accounting",
        "provider_diagnostics.public_subscription_count",
        "provider_diagnostics.live_emitted_count",
        "provider_diagnostics.synthetic_frame_count",
        "prefetched_after_deadline_count",
        "deadline_prefetch_count_complete",
    ],
)
def test_realtime_case_requires_complete_frozen_accounting(
    tmp_path, monkeypatch, missing
):
    manifest = _manifest(tmp_path)
    parts = missing.split(".")
    target = manifest
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("provider_diagnostics", "live_emitted_count", 3),
        ("frame_accounting", "unaccounted_frames", 1),
        ("frame_accounting", "emitted_minus_records", True),
        ("provider_diagnostics", "public_subscription_count", 2),
        ("provider_diagnostics", "control_frame_count", 4),
        ("provider_diagnostics", "websocket_frame_count", 4),
        ("provider_diagnostics", "parsed_event_count", 1),
    ],
)
def test_realtime_case_rejects_inconsistent_accounting(
    tmp_path, monkeypatch, section, key, value
):
    manifest = _manifest(tmp_path)
    accounting = manifest["provider_inspection"]["frame_accounting"]
    if section == "provider_diagnostics":
        manifest[section][key] = value
        accounting[key] = value
    else:
        accounting[key] = value
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("prefetched_after_deadline_count",), value)
        for value in (None, True, -1, 1.5, "PRIVATE")
    ]
    + [
        (("deadline_prefetch_count_complete",), value)
        for value in (None, False, 1, "PRIVATE")
    ]
    + [
        (("provider_diagnostics",), []),
        (("provider_inspection", "frame_accounting"), []),
        (("provider_inspection", "records"), True),
        (("provider_inspection", "records"), 3),
        (("provider_inspection", "frame_accounting", "live_emitted_count"), 3),
    ],
)
def test_realtime_case_rejects_invalid_or_unfrozen_delivery_accounting(
    tmp_path, monkeypatch, path, value
):
    manifest = _manifest(tmp_path)
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, "realtime", [], expected_success=True, timeout=1)
    assert result["passed"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_stdout_report_survives_failed_atomic_publication(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(smoke, "_run_case", lambda *args, **kwargs: {"passed": True})

    def fail_sync(_descriptor):
        msg = "failed to sync report"
        raise OSError(msg)

    monkeypatch.setattr(
        atomic_files,
        "os",
        SimpleNamespace(**(vars(os) | {"fsync": fail_sync})),
    )
    directory = tmp_path / "artifacts"
    with pytest.raises(OSError, match="failed to sync report"):
        smoke.main(
            ["--run-network", "--channel", "selected", "--artifacts", str(directory)]
        )

    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert not (directory / "report.json").exists()
    assert all(path.is_dir() for path in directory.iterdir())


def test_atomic_report_survives_failed_stdout(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "_run_case", lambda *args, **kwargs: {"passed": True})

    def fail_print(*args, **kwargs):
        raise BrokenPipeError

    monkeypatch.setattr(smoke, "print", fail_print, raising=False)
    directory = tmp_path / "artifacts"
    with pytest.raises(BrokenPipeError):
        smoke.main(
            ["--run-network", "--channel", "selected", "--artifacts", str(directory)]
        )

    assert json.loads((directory / "report.json").read_text())["passed"] is True

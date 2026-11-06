# SPDX-License-Identifier: MIT

"""Smoke orchestration fails closed and never prints child text or proxies."""

from __future__ import annotations

import json
import stat
import subprocess
from types import SimpleNamespace

import pytest

from chat_downloader.sites.kick.capture_inspection import inspect_capture
from chat_downloader.sites.kick.live_service import _KickLiveDiagnostics
from scripts import smoke_kick_live as smoke


def _manifest(directory, *, diagnostics=None, prefetched=0, **changes):
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
        "message_type_counts": {"text_message": count} if count else {},
        "success": True,
        "parity_status": "passed",
        "provider_diagnostics": state,
        "prefetched_after_deadline_count": prefetched,
        "deadline_prefetch_count_complete": True,
    }
    capture = directory / "chat.jsonl"
    capture.write_text(
        "".join(
            json.dumps(
                {
                    "message_type": "text_message",
                    "message_id": str(index),
                    "timestamp": index,
                    "author": {"id": "author"},
                    "message": "PRIVATE_CHAT_TEXT",
                }
            )
            + "\n"
            for index in range(count)
        )
    )
    data["provider_inspection"] = inspect_capture(capture, run_summary=data)
    data.update(changes)
    (directory / "manifest.json").write_text(json.dumps(data))
    return data


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
    )
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    result = smoke._run_case(tmp_path, case, [], expected_success=True, timeout=1)
    assert result["passed"] is True
    assert result["records"] == 0


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

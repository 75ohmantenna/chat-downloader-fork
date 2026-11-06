# SPDX-License-Identifier: MIT

"""Exclusive, content-free run reports and replay completeness policy."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.metadata import __version__
from chat_downloader.redaction import sanitize_for_log
from chat_downloader.request_profiles import REQUEST_PROFILES
from chat_downloader.sites.output_dispatch import _expand_output_file_name
from chat_downloader.sites.proxy import TLS_DIAGNOSTIC_CIPHERS, TLS_DIAGNOSTIC_VERSIONS
from chat_downloader.utils.atomic_files import atomic_text_file
from chat_downloader.utils.time_utils import ensure_seconds

if TYPE_CHECKING:
    from collections.abc import Mapping

    from chat_downloader.sites.models import Chat

    from .runner import RunResult

type _ArtifactSignature = tuple[tuple[int, int], str]


def is_completed_replay(chat: Chat | None) -> bool:
    """Ask the owning provider whether the chat is a stable completed replay."""
    if chat is None:
        return False
    classifier = getattr(chat.site, "is_completed_replay_status", None)
    if classifier is None:
        # Preserve compatibility for direct Chat use and third-party site
        # adapters that predate the provider capability.
        return chat.status == "completed"
    return bool(classifier(chat.status))


def replay_complete(chat: Chat | None, result: RunResult) -> bool:
    """Require successful exhaustion with no loss in the provider observation."""
    if chat is None or not is_completed_replay(chat) or not result.success:
        return False
    state = result.provider_diagnostics
    return (
        result.termination_reason in {"completed", "empty_window"}
        and state.get("history_complete", True) is True
        and not has_record_loss(state)
    )


def has_record_loss(state: Mapping[str, object]) -> bool:
    """Keep known loss sticky across checkpoints and completion reports."""
    return any(
        state.get(key, 0)
        for key in (
            "malformed_timestamp",
            "malformed_object",
            "parse_error",
            "prior_record_loss",
        )
    )


def _manifest_diagnostics(state: Mapping[str, object]) -> dict[str, object]:
    """Select bounded capture counters without retaining arbitrary adapter data."""
    counters: dict[str, object] = {
        key: state[key]
        for key in (
            "poll_count",
            "processed_action_count",
            "emitted_message_count",
            "channel_overlap_suppressed_count",
            "parse_error",
            "continuation_request_count",
            "continuation_retry_count",
            "http_error_count",
            "network_error_count",
            "json_error_count",
            "bootstrap_request_count",
            "bootstrap_http_error_count",
            "bootstrap_network_error_count",
            "bootstrap_fallback_count",
            "bootstrap_profile_switch_count",
            "continuation_profile_switch_count",
            "replay_timestamp_missing_count",
            "replay_zero_offset_without_timestamp_count",
            "replay_repeated_page_count",
            "initial_replay_recovery_attempt_count",
            "initial_replay_recovery_success_count",
            "websocket_frame_count",
            "control_frame_count",
            "parsed_event_count",
            "unsupported_event_count",
            "unknown_message_type_count",
            "malformed_event_count",
            "invalid_websocket_frame_count",
            "websocket_reconnect_count",
            "pusher_error_count",
            "pusher_key_recovery_count",
            "pusher_connection_count",
            "centrifugo_connection_count",
            "public_state_poll_count",
            "public_state_poll_failure_count",
            "public_subscription_count",
            "synthetic_frame_count",
            "preloaded_emitted_count",
            "startup_backfill_emitted_count",
            "history_recovery_failure_count",
            "live_emitted_count",
            "optional_metadata_degradation_count",
            "optional_metadata_primary_team_service_error_count",
            "connection_setup_duration_ms",
            "connection_setup_max_duration_ms",
            "reconnect_outage_duration_ms",
            "reconnect_outage_max_duration_ms",
            "connection_attempt_count",
            "connection_success_count",
            "connection_setup_failure_count",
            "reconnect_count",
            "server_reconnect_requested_count",
            "received_irc_chunk_count",
            "received_irc_frame_count",
            "benign_irc_control_frame_count",
            "parsed_irc_message_count",
            "receive_timeout_count",
            "idle_watchdog_expiration_count",
            "keepalive_ping_sent_count",
            "keepalive_ping_received_count",
            "keepalive_pong_sent_count",
            "keepalive_pong_received_count",
            "duplicate_message_suppressed_count",
            "filtered_message_count",
            "reconnect_backfill_emitted_count",
            "reconnect_backfill_truncated_count",
            "reconnect_backfill_truncated_microseconds",
        )
        if type(value := state.get(key)) is int and value >= 0
    }
    for key in ("initial_request_profile", "active_request_profile"):
        value = state.get(key)
        if isinstance(value, str) and value in REQUEST_PROFILES:
            counters[key] = value
    for key, allowed in (
        ("irc_tls_version", TLS_DIAGNOSTIC_VERSIONS),
        ("irc_tls_cipher", TLS_DIAGNOSTIC_CIPHERS),
    ):
        value = state.get(key)
        if isinstance(value, str) and value in allowed:
            counters[key] = value
    view = state.get("chat_view")
    if isinstance(view, str) and view in {
        "Top chat",
        "Live chat",
        "Top chat replay",
        "Live chat replay",
    }:
        counters["chat_view"] = view
    reasons = state.get("non_emission_counts")
    if isinstance(reasons, dict):
        counters["non_emission_counts"] = {
            key: reasons[key]
            for key in (
                "known ignored/control actions",
                "known ignored renderers",
                "unparsed actions",
                "invalid messages",
                "message type/group filtered",
                "time-range filtered",
                "time-range stop",
            )
            if type(value := reasons.get(key)) is int and value >= 0
        }
    return counters


def _capture_outputs(chat: Chat, *, keep_created: bool = False) -> list[Path]:
    """Expand lazy paths while retaining opened artifact names when requested."""
    dispatcher = chat._output_dispatcher
    if dispatcher is None:
        return []
    return [
        Path(
            item["file_name"]
            if keep_created and item["file_created"]
            else _expand_output_file_name(
                item["file_name"], title=chat.title, video_id=chat.id
            )
        )
        for item in dispatcher.writer_summaries
    ]


def _time_window(parameters: Mapping[str, object]) -> dict[str, float | None]:
    """Normalize request bounds without persisting invalid or arbitrary values."""
    return {
        key: ensure_seconds(value)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
        else None
        for key in ("start_time", "end_time")
        for value in (parameters.get(key),)
    }


def _effective_window(
    chat: Chat | None, parameters: Mapping[str, object]
) -> dict[str, object] | None:
    """Report only fixed, normalized retrieval metadata after successful setup."""
    if chat is None:
        return None
    window = chat._request_window if chat._request_window is not None else parameters
    return {
        **_time_window(window),
        "time_basis": "clip" if window.get("time_basis") == "clip" else "recording",
    }


def _artifact_signature(
    path: Path, expected: tuple[int, int] | None = None
) -> _ArtifactSignature:
    """Hash a validated regular file and retain the identity actually read."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        msg = "Capture artifacts must be regular files with exactly one link."
        raise ValueError(msg)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or identity != (before.st_dev, before.st_ino)
            or (expected is not None and identity != expected)
        ):
            msg = "Manifest artifact identity changed after capture."
            raise ValueError(msg)
        return identity, hashlib.file_digest(source, "sha256").hexdigest()


class RunManifest:
    """Validate artifact separation before capture; never replace an existing file."""

    def __init__(
        self,
        name: str,
        resume: str | None,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> None:
        """Reject existing destinations and reserved checkpoint paths."""
        self.path = Path(name).absolute()
        if self.path.exists() or self.path.is_symlink():
            msg = "Run manifest requires a new file."
            raise ValueError(msg)
        if resume and self.path.resolve() in {
            Path(resume).resolve(),
            Path(resume + ".lock").resolve(),
        }:
            msg = "Run manifest must be distinct from the checkpoint."
            raise ValueError(msg)
        if not self.path.parent.is_dir():
            msg = (
                f"Run manifest parent directory must already exist: {self.path.parent}"
            )
            raise ValueError(msg)
        self.valid = True
        self._parameters = parameters if parameters is not None else {}
        self.selected_window = _time_window(self._parameters)

    def bind(self, chat: Chat) -> None:
        """Resolve lazy names before any output writer can open its file."""
        self.valid = False
        outputs = _capture_outputs(chat)
        destination = self.path.resolve()
        if any(path.resolve() == destination for path in outputs):
            msg = "Run manifest must be distinct from chat outputs."
            raise ValueError(msg)

        self.valid = True

    def write(
        self,
        chat: Chat | None,
        result: RunResult,
        *,
        verified_artifacts: Mapping[Path, _ArtifactSignature] | None = None,
    ) -> None:
        """Write closed artifacts and outcome using the observed provider counters."""
        if not self.valid:
            msg = "Run manifest output paths were not validated."
            raise ValueError(msg)
        dispatcher = getattr(chat, "_output_dispatcher", None)
        writers = dispatcher.writer_summaries if dispatcher else []
        # Lazy channel discovery can change metadata after preflight. Opened
        # writers retain the actual path, including literal braces in titles.
        outputs = _capture_outputs(chat, keep_created=True) if chat is not None else []
        if any(path.resolve() == self.path.resolve() for path in outputs):
            msg = "Run manifest must be distinct from chat outputs."
            raise ValueError(msg)
        artifacts: list[dict[str, object]] = []
        for path, writer, output_writer in zip(
            outputs, writers, dispatcher.writers if dispatcher else (), strict=True
        ):
            signature = None
            # Unopened files can predate this run: never certify them as output.
            previous = (
                verified_artifacts.get(path.absolute())
                if verified_artifacts is not None
                else None
            )
            if writer["file_created"] or previous is not None:
                writer_identity = getattr(output_writer, "_file_identity", None)
                expected = writer_identity or (previous[0] if previous else None)
                observed = _artifact_signature(path, expected)
                if previous is not None and observed != previous:
                    msg = "Manifest artifact changed after checkpoint verification."
                    raise ValueError(msg)
                signature = observed[1]
            artifacts.append({**writer, "sha256": signature})
        payload = {
            "schema_version": 1,
            "program_version": __version__,
            "success": result.success,
            "replay_complete": replay_complete(chat, result),
            "termination_reason": result.termination_reason,
            "failure_stage": result.failure_stage,
            "selected_window": self.selected_window,
            "effective_window": _effective_window(chat, self._parameters),
            "parity_status": result.parity_status,
            "provider_inspection": result.provider_inspection,
            "message_count": result.message_count,
            "prior_message_count": result.provider_diagnostics.get(
                "prior_message_count", 0
            ),
            "message_type_counts": result.message_type_counts,
            "elapsed_seconds": result.elapsed_seconds,
            "provider_diagnostics": _manifest_diagnostics(result.provider_diagnostics),
            "prefetched_after_deadline_count": result.prefetched_after_deadline_count,
            "deadline_prefetch_count_complete": result.deadline_prefetch_count_complete,
            "recording": {
                "site_name": getattr(getattr(chat, "site", None), "_NAME", None),
                **{
                    key: getattr(chat, key, None)
                    for key in ("id", "start_time", "duration", "status")
                },
            },
            "outputs": artifacts,
        }
        encoded = json.dumps(sanitize_for_log(payload), allow_nan=False, indent=2)
        # Stage privately; publish complete JSON without replacing a late arrival.
        with atomic_text_file(self.path) as target:
            target.write(encoded)
            target.write("\n")
            target.flush()
            # This outcome comparison commits the report. A first CLI signal
            # during hashing, encoding, staging, or writing changes the run
            # outcome synchronously; correct only this private staging file.
            # Signals after this point do not revise the committed report.
            if (
                payload["success"] != result.success
                or payload["termination_reason"] != result.termination_reason
                or payload["failure_stage"] != result.failure_stage
            ):
                payload.update(
                    success=result.success,
                    replay_complete=replay_complete(chat, result),
                    termination_reason=result.termination_reason,
                    failure_stage=result.failure_stage,
                )
                target.seek(0)
                target.write(
                    json.dumps(sanitize_for_log(payload), allow_nan=False, indent=2)
                )
                target.write("\n")
                target.truncate()


def validate_complete_request(chat: Chat) -> None:
    """Reject live and unknown replay states before lazy output opens."""
    if not is_completed_replay(chat):
        msg = "Complete capture requires a completed replay."
        raise ValueError(msg)

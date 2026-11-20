# SPDX-License-Identifier: MIT

"""Counter names and legacy defaults for Kick live summaries and inspection."""

from __future__ import annotations

# Keep the published counter order. None marks counters required in every summary.
LIVE_COUNTER_DEFAULTS: dict[str, int | None] = {
    "websocket_frame_count": None,
    "control_frame_count": None,
    "parsed_event_count": None,
    "unsupported_event_count": None,
    "unknown_message_type_count": None,
    "malformed_event_count": None,
    "invalid_websocket_frame_count": None,
    "websocket_reconnect_count": None,
    "pusher_error_count": None,
    "pusher_key_recovery_count": None,
    "pusher_connection_count": 0,
    "centrifugo_connection_count": 0,
    "public_state_poll_count": 0,
    "public_state_poll_failure_count": 0,
    "public_subscription_count": 0,
    "synthetic_frame_count": 0,
    "preloaded_emitted_count": None,
    "startup_backfill_emitted_count": 0,
    "history_recovery_failure_count": 0,
    "live_emitted_count": None,
    "reconnect_backfill_emitted_count": None,
    "reconnect_backfill_truncated_count": 0,
    "reconnect_backfill_truncated_microseconds": 0,
}

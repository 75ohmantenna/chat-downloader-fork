# SPDX-License-Identifier: MIT

"""YouTube continuation-loop state management.

Public surface
--------------
- :class:`ContinuationLoopState` — mutable per-iteration state.
- :func:`build_continuation_params` — build the POST body for the next request.
- :func:`derive_live_offset_milliseconds` — derive live player offset from
  timestamps.
- :func:`enrich_live_message_timing` — backfill live timing fields on messages.
- :func:`extract_visitor_data` — pull visitor-data token from an API response.
- :func:`get_live_start_time_ms` — capture the live offset baseline.
- :func:`update_state_from_result` — advance state after parsing a response.
"""

from __future__ import annotations

from .continuation_loop_runtime import (
    build_continuation_params,
    derive_live_offset_milliseconds,
    enrich_live_message_timing,
    extract_visitor_data,
    get_live_start_time_ms,
    update_state_from_result,
)
from .continuation_loop_state import ContinuationLoopState

__all__ = [
    "ContinuationLoopState",
    "build_continuation_params",
    "derive_live_offset_milliseconds",
    "enrich_live_message_timing",
    "extract_visitor_data",
    "get_live_start_time_ms",
    "update_state_from_result",
]

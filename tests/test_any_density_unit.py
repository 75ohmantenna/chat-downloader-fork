# SPDX-License-Identifier: MIT

"""Non-regression floor: no module may exceed its Any-density baseline.

The per-round lowering ritual is retired (X4). These baselines are now a
frozen floor — do not raise them. Opportunistic tightening is welcome when
a typing migration happens alongside feature work, but there is no
scheduled lowering cadence.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "chat_downloader"
_ANY = re.compile(r"\bAny\b")
DEFAULT_CAP = 2

# Baseline captured after round-3 Step G5 migrations (2026-06); frozen at X4.
# Counts are total occurrences of `Any` in each file (not line count).
# Files at DEFAULT_CAP or below are omitted — they are implicitly capped.
# Every entry above DEFAULT_CAP is a genuine payload/accumulator boundary;
# do not raise these values. Tighten opportunistically alongside typing work.
# See docs/maintenance-notes.md "Round-3 typing pass" for context.
BASELINE: dict[str, int] = {
    # Format spec objects loaded from JSON config files (dict[str,Any] is the
    # stable boundary between the JSON loader and the formatter internals).
    "formatting/format.py": 33,
    # Shared site infrastructure — heterogeneous field types resist TypedDict.
    "sites/base.py": 14,
    "sites/models.py": 13,
    # Dispatcher callbacks/writer protocol — dict[str,Any] is the chat-item
    # boundary (moved from sites/models.py in M1).
    "sites/output_dispatch.py": 9,
    "sites/session.py": 7,
    "sites/remap.py": 10,
    "sites/filters.py": 3,
    # Twitch site-specific accumulators and remapping tables.
    # X11 migrated discovery/graphql_client/replay_service/replay_transport/
    # live_service off dict[str,Any] JSON boundaries to json_types aliases.
    # Residuals per X-series taxonomy: Callable[...,Any] transport params,
    # badge accumulators (types.py containers), IRC-tag accumulators (V3-
    # declined), frozen public params, assembled-output dicts.
    "sites/twitch/replay_service.py": 3,
    "sites/twitch/extractor.py": 18,
    "sites/twitch/discovery.py": 10,
    "sites/twitch/parsing/message_irc_resolve.py": 14,
    "sites/twitch/parsing/messages.py": 14,
    "sites/twitch/graphql_client.py": 7,
    "sites/twitch/remappings.py": 11,
    "sites/twitch/parsing/badges.py": 9,
    "sites/twitch/irc_transport.py": 6,
    "sites/twitch/replay_transport.py": 3,
    "sites/twitch/types.py": 5,
    "sites/twitch/parsing/message_emotes.py": 5,
    "sites/twitch/live_service.py": 4,
    # YouTube site-specific accumulators and remapping tables.
    "sites/youtube/_protocols.py": 18,
    "sites/youtube/client_requests_bootstrap.py": 3,
    "sites/youtube/parsing/message_content_text_parser.py": 7,
    "sites/youtube/parsing/message_items_content_parser.py": 10,
    "sites/youtube/parsing/actions_router.py": 9,
    "sites/youtube/helpers.py": 2,
    "sites/youtube/discovery_channels_runtime_iteration.py": 9,
    "sites/youtube/video_metadata.py": 4,
    "sites/youtube/parsing/message_content_badges.py": 3,
    "sites/youtube/discovery_helpers.py": 6,
    "sites/youtube/client_context.py": 6,
    "sites/youtube/video_status.py": 8,
    "sites/youtube/discovery_playlists.py": 8,
    # Error/retry helpers extracted from client_requests_continuation (U1).
    # T2 helper adds dict[str,Any] param; Any import duplicated across split.
    "sites/youtube/client_requests_errors.py": 2,
    "sites/youtube/client_auth.py": 6,
    "sites/youtube/chat_streams_runtime_iteration.py": 2,
    "sites/youtube/chat_streams_context.py": 2,
    "sites/youtube/parsing/actions_handlers_validation.py": 5,
    "sites/youtube/client_requests_initial.py": 4,
    "sites/youtube/video_status_helpers.py": 6,
    "sites/youtube/chat_streams.py": 4,
    "sites/youtube/message_pipeline.py": 5,
    "sites/youtube/chat_users_router.py": 5,
    "sites/youtube/video_initialization.py": 3,
    "sites/youtube/extractor.py": 3,
    "sites/youtube/constants_message.py": 3,
    "sites/youtube/chat_users_retrieval.py": 3,
    # Output and formatting layers — writers receive heterogeneous items.
    "output/writers.py": 13,
    "output/csv_rewrite.py": 6,
    "output/continuous_write.py": 3,
    # Utilities — generic helpers require Any for cross-type dispatch.
    "utils/dict_utils.py": 19,
    "utils/timed_generator.py": 12,
    "utils/timed_input.py": 3,
    "utils/conversion_utils.py": 12,
    "utils/json_utils.py": 11,
    "utils/console_utils.py": 7,
    "utils/filename_utils.py": 0,
    "utils/string_utils.py": 6,
    "utils/time_utils.py": 3,
    # Runtime layer — orchestration and CLI glue.
    "runtime/session_lifecycle.py": 5,
    "runtime/runner.py": 4,
    "runtime/cli_bridge.py": 5,
    "runtime/chat_pipeline.py": 5,
    "runtime/site_dispatch.py": 4,
    # Models layer.
    "models/_request.py": 5,
    "models/_base.py": 5,
    "models/_runconfig.py": 4,
    # Top-level modules.
    "debugging.py": 3,
    # Redaction-payload boundary — sanitize_for_log/capture_debug_sample accept
    # Any because incoming payloads are untyped (moved from debugging.py in M2).
    "redaction.py": 6,
    "chat_downloader.py": 5,
    "cli_args.py": 4,
    "request_profiles.py": 3,
}


def _count(path: Path) -> int:
    return len(_ANY.findall(path.read_text(encoding="utf-8")))


def test_any_density_within_baseline() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        cap = BASELINE.get(rel, DEFAULT_CAP)
        n = _count(path)
        if n > cap:
            offenders.append(f"{rel}: {n} > {cap}")
    assert not offenders, "Any-density over baseline:\n" + "\n".join(offenders)

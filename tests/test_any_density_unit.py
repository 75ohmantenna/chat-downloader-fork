# SPDX-License-Identifier: MIT

"""Non-regression floor: no module may exceed its Any-density baseline.

The per-round lowering ritual is retired (Round-10.4). These baselines are now a
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

# Baseline captured after Round-03 typing migrations; frozen at Round-10.4 (2026-06).
# Counts are total occurrences of `Any` in each file (not line count).
# Files at DEFAULT_CAP or below are omitted — they are implicitly capped.
# Every entry above DEFAULT_CAP is a genuine payload/accumulator boundary;
# do not raise these values. Tighten opportunistically alongside typing work.
# See docs/maintenance-notes.md "Round-03 — Typing pass" for context.
BASELINE: dict[str, int] = {
    # Format spec objects loaded from JSON config files (dict[str,Any] is the
    # stable boundary between the JSON loader and the formatter internals).
    "formatting/format.py": 33,
    # Shared site infrastructure — heterogeneous field types resist TypedDict.
    "sites/base.py": 14,
    "sites/models.py": 13,
    # Dispatcher callbacks/writer protocol — dict[str,Any] is the chat-item
    # boundary (moved from sites/models.py in Round-04.1).
    "sites/output_dispatch.py": 9,
    "sites/session.py": 7,
    "sites/remap.py": 10,
    "sites/filters.py": 3,
    # Twitch site-specific accumulators and remapping tables.
    # Round-11.5 migrated discovery/graphql_client/replay_service/replay_transport/
    # live_service off dict[str,Any] JSON boundaries to json_types aliases.
    # Round-15 narrowed transport Callable[...,Any] seams to Protocols and precise
    # Callable signatures (live_service irc_factory/message_generator,
    # replay_service fetch_messages/_fetch_gql_one, replay_transport signatures,
    # graphql_client and discovery session/download_gql_func callables).
    # Residuals per the Round-11 taxonomy: badge accumulators (types.py containers),
    # IRC-tag accumulators (Round-08.3-declined), frozen public params,
    # assembled-output dicts.
    "sites/twitch/replay_service.py": 0,
    "sites/twitch/extractor.py": 18,
    "sites/twitch/parsing/message_irc_resolve.py": 14,
    "sites/twitch/parsing/messages.py": 14,
    "sites/twitch/graphql_client.py": 3,
    "sites/twitch/discovery.py": 4,
    "sites/twitch/remappings.py": 11,
    "sites/twitch/parsing/badges.py": 9,
    "sites/twitch/irc_transport.py": 6,
    "sites/twitch/replay_transport.py": 0,
    "sites/twitch/types.py": 5,
    "sites/twitch/parsing/message_emotes.py": 5,
    "sites/twitch/live_service.py": 3,
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
    # Error/retry helpers extracted from client_requests_continuation (Round-07.1).
    # Round-06.2 helper adds dict[str,Any] param; Any import duplicated across split.
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
    "output/writers.py": 9,
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
    # Kick site — transport/session callables, injectable Callable[...,Any]
    # seams, and assembled-output accumulators (info/metadata/author) remain Any.
    # Round-13: parsing-layer params narrowed from Any to object/Mapping[str,object]
    # (param-annotation-only; extraction logic via _opt_str unchanged).
    # Round-14: HTTP-response boundaries (api_client) and websocket-frame boundary
    # (websocket_transport) migrated to JSONAny/JSONDict/JSONList + cast().
    # Round-15: transport/service Callable[...,Any] seams narrowed to Protocols
    # (websocket_transport connector, live_service frame_iterator, twitch live
    # irc_factory/message_generator, replay fetch_messages/transport signatures).
    # api_client.py retains Any from the curl-cffi/cloudscraper session backend
    # (the multi-tier impersonation session is typed Any).
    # Residuals per Round-11/13/14/15 taxonomy: assembled-output dicts.
    # emotes.py: inputs already typed (content: str); all Any are output accumulators.
    # extractor.py: params: ChatRequest|dict[str,Any] x4 frozen public API; ClassVar
    # data tables x2; import -- all intentional residuals, no code change.
    "sites/kick/parsing/messages.py": 10,
    "sites/kick/websocket_transport.py": 0,
    "sites/kick/live_service.py": 3,
    "sites/kick/parsing/events.py": 3,
    "sites/kick/api_client.py": 6,
    "sites/kick/parsing/emotes.py": 4,
    "sites/kick/parsing/subscriptions.py": 9,
    "sites/kick/parsing/moderation.py": 13,
    "sites/kick/parsing/pins.py": 8,
    "sites/kick/parsing/hosts.py": 5,
    "sites/kick/replay_service.py": 5,
    "sites/kick/extractor.py": 7,
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

# Capability Inventory

Preservation checklist for behavior-preserving maintenance. Use this before
large refactors, parser reshaping, or tooling changes.

## Core Capabilities

| Capability | Implementation owner | Guardrails |
| --- | --- | --- |
| CLI parsing, generated help, logging/testing flags, headers | `cli.py`, `cli_args.py`, `models/` metadata | `tests/test_cli.py`, `tests/test_cli_help_unit.py`, `tests/test_cli_bridge_unit.py`, signal-handler tests |
| Python API: `ChatDownloader`, `run`, dataclasses, exports | `chat_downloader.py`, `models/`, package `__init__.py` files | model tests, facade tests, public API snapshots, facade-param sync |
| URL normalization, dispatch, and site defaults | `runtime/site_dispatch.py` (`dispatch_chat`), `sites/base.py`, `models.SiteDefault` | dispatch composition, schemeless/protocol-relative URL matching, session lifecycle, `SiteDefault` identity |
| YouTube bootstrap, live/replay/clip continuations, parsing | `sites/youtube/` | YouTube unit tests, live-event fixtures, continuation fixtures, drift harness |
| YouTube channel, handle, and playlist discovery | `sites/youtube/discovery.py`, `discovery_playlists.py` | YouTube discovery unit and downloader-assembly tests |
| YouTube bootstrap/profile diagnostics and automatic/offline record/manifest inspection | `sites/youtube/client_requests_bootstrap.py`, `capture_inspection.py`, `scripts/inspect_youtube_capture.py` | challenged-replay composition, bootstrap-counter, capture-inspection and capture-verification tests |
| Twitch live IRC, content-free connection and optional-metadata-degradation diagnostics, typed paid-pin metadata, and current notice groups | `sites/twitch/live_service.py`, `graphql_client.py`, `irc_transport.py`, `irc_diagnostics.py`, `parsing/`, `remappings.py`, `constants.py` | live-service, GraphQL client, transport, current-event fixtures, IRC parser, drift harness tests |
| Twitch VOD and clip replay | `sites/twitch/replay_service.py`, `_replay_vod_loop.py`, `replay_transport.py` | replay service, legacy/mobile replay transport, VOD edge tests |
| Twitch GraphQL hashes and bounded document fallbacks, badge operation fallbacks, Client-ID | `graphql_client.py`, `badge_client.py`, `constants.py`, `types.py`, `parsing/badges.py` | Twitch client, hash/document fallback, badge-client, and badge-purity tests |
| Kick anonymous negotiated Pusher/Centrifugo chat and public lifecycle feeds, cached deleted-message text, current pin, snapshots, bounded reconnect backfill, and diagnostics | `sites/kick/live_service.py`, `deleted_message_cache.py`, `public_transport.py`, `centrifugo_transport.py`, `realtime_connection.py`, `public_state.py`, `live_iterator.py`, `history.py`, `websocket_transport.py`, `api_client.py`, `parsing/` | Kick public-realtime, live-service, deleted-message cache, history, transport, api-client, parsing unit tests |
| Kick VOD and clip replay, including anonymous mobile clip-metadata fallback | `sites/kick/replay_service.py`, `clip_service.py`, `history.py`, `request_retry.py`, `api_client.py` | Kick replay-service, history, request-retry, clip-service, and api-client tests |
| Kick Pusher key discovery, Cloudflare and country-block handling, event/group maps | `sites/kick/pusher_discovery.py`, `constants.py`, `api_client.py`, `parsing/events.py` | Kick pusher-discovery, extractor, api-client, parsing-events tests |
| Output formats: JSONL and TXT; placeholder expansion; path/inode deduplication; unsupported extensions rejected | `output/continuous_write.py`, `output/writers.py`, `runtime/chat_pipeline.py`, `sites/output_dispatch.py` | output writer, continuous-write, JSONL UTC, multiple-output and expanded-alias tests |
| Paid/ticker semantic deduplication for formatted outputs; lossless raw output | `sites/_message_dedup.py`, `sites/output_dispatch.py`, `runtime/runner.py` | deduplication, chat-model, runner, and mixed-output tests |
| Replay shutdown checkpoints with verified request/artifact identity, accepted-record rollback, and overlap deduplication | `runtime/capture_checkpoint.py`, `sites/output_dispatch.py` | checkpoint writer-composition and failed-recovery tests |
| Optional automatic JSONL/TXT verification, including safely closed interrupted captures, and failure summaries | `runtime/capture_verification.py`, `runtime/runner.py` | checkpoint/parity composition and runner tests |
| Offline JSONL/TXT capture parity audit with exact formatting, semantic deduplication, physical-line validation, and content-free diagnostics | `output/capture_parity.py`, `scripts/audit_capture_parity.py` | capture-parity subprocess and writer-composition tests |
| Automatic Kick live inspection plus offline live/replay inspection and debug-summary reconciliation | `sites/kick/capture_inspection.py`, `scripts/inspect_kick_capture.py` | Kick capture-verification composition, capture-inspection subprocess, and recorded-replay tests |
| Filtering, provider-aware text formatting, conditional/singular format fields, time windows | `sites/filters.py`, `formatting/format.py`, `formatting/custom_formats.json`, `runtime/chat_pipeline.py` (`configure_chat`) | filtering, formatting, provider-format, configured-chat composition |
| Cookies, sessions, auth, explicit/environment proxy safety | `ChatDownloader`, `_SiteSessionPool`, `ChatDownloaderSession`, `runtime/config_guards.py`, `sites/proxy.py`, YouTube auth | HTTP adapter, downloader lifecycle, proxy transport, auth, facade redaction |
| Retry, timeout, record-consistent CLI interruption, cleanup | `sites/retry.py`, `utils/retry_utils.py`, `utils/interrupts.py`, `runtime/runner.py`, `Chat.close`, `TimedGenerator` | retry, network-retry, runner, chat-model, live-service, timed-generator, interrupt-accounting tests |
| Debug logging, custom-header redaction, bounded drift/clean-run sample capture, final run summaries | `debugging.py`, `redaction.py`, `debug_sample_utils.py`, `runtime/runner.py` | debugging, redaction, debug-sample, runner, provider-diagnostic tests |
| Build, spelling, install, smoke, import boundaries | `Makefile`, `pyproject.toml`, GitHub Actions | codespell, `tests/test_makefile_contract_unit.py`, import-linter, release metadata, `make ci` |

## Preservation Rules

- Add or update a focused regression test before changing parser behavior,
  retry behavior, output persistence, runtime orchestration, or public API
  shape.
- Promote raw platform drift examples into `tests/fixtures/` before reshaping
  YouTube, Twitch, or Kick parser logic.
- Keep old public import paths working unless a deliberate compatibility break
  is documented and tested.
- Do not weaken spelling, coverage, import-linter, Any-density, module-size,
  complexity, build, smoke, or network-separation guardrails without documented
  rationale.
- Reopen closed maintenance deferrals only with new evidence, such as a third
  site, a failing capability, or an organic threshold breach during feature
  work.

YouTube, Twitch live, and Kick live `--verify_output` combine exact parity with content-free
provider inspection from their respective `capture_inspection.py` modules. The
report is shared by the run result, debug summary, and run manifest. Kick replay
inspection remains an offline command that can reconcile multiple resumed runs.

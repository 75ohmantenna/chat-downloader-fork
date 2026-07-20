# Capability Inventory

Preservation checklist for behavior-preserving maintenance. Use this before
large refactors, parser reshaping, or tooling changes.

## Core Capabilities

| Capability | Implementation owner | Guardrails |
| --- | --- | --- |
| CLI parsing, generated help, logging/testing flags, headers | `cli.py`, `cli_args.py`, `models/` metadata | `tests/test_cli.py`, `tests/test_cli_help_unit.py`, `tests/test_cli_bridge_unit.py`, signal-handler tests |
| Python API: `ChatDownloader`, `run`, dataclasses, exports | `chat_downloader.py`, `models/`, package `__init__.py` files | model tests, facade tests, public API snapshots, facade-param sync |
| URL dispatch and site defaults | `runtime/site_dispatch.py` (`dispatch_chat`), `sites/base.py`, `models.SiteDefault` | dispatch composition, URL matching, session lifecycle, `SiteDefault` identity |
| YouTube bootstrap, live/replay/clip continuations, parsing | `sites/youtube/` | YouTube unit tests, live-event fixtures, continuation fixtures, drift harness |
| YouTube channel, handle, and playlist discovery | `sites/youtube/discovery.py`, `discovery_playlists.py` | YouTube discovery unit and downloader-assembly tests |
| Twitch live IRC | `sites/twitch/live_service.py`, `irc_transport.py`, `parsing/` | live-service, transport, IRC parser, drift harness tests |
| Twitch VOD and clip replay | `sites/twitch/replay_service.py`, `_replay_vod_loop.py`, `replay_transport.py` | replay service, replay transport, VOD edge tests |
| Twitch GraphQL hashes, badges, Client-ID | `graphql_client.py`, `constants.py`, `types.py`, `parsing/badges.py` | Twitch client, hash coverage, badge-purity tests |
| Kick live Pusher chat | `sites/kick/live_service.py`, `websocket_transport.py`, `api_client.py`, `http_session.py`, `parsing/` | Kick live-service, transport, api-client, parsing unit tests |
| Kick VOD replay | `sites/kick/replay_service.py`, `api_client.py` | Kick replay-service and api-client tests |
| Kick Pusher key discovery, Cloudflare handling, event/group maps | `sites/kick/pusher_discovery.py`, `constants.py`, `api_client.py`, `parsing/events.py` | Kick pusher-discovery, extractor, api-client, parsing-events tests |
| Output formats: JSONL and TXT; unsupported extensions rejected | `output/continuous_write.py`, `output/writers.py` | output writer, continuous-write, JSONL UTC, multiple-output tests |
| Paid/ticker semantic dedup for formatted outputs; lossless raw output | `sites/_message_dedup.py`, `sites/output_dispatch.py`, `runtime/runner.py` | deduplication, chat-model, runner, and mixed-output tests |
| Filtering, formatting, time windows | `sites/filters.py`, `formatting/format.py`, `runtime/chat_pipeline.py` (`configure_chat`) | filtering, formatting, configured-chat composition |
| Cookies, sessions, auth, proxy safety | `ChatDownloader`, `_SiteSessionPool`, `ChatDownloaderSession`, `sites/proxy.py`, YouTube auth | HTTP adapter, downloader lifecycle, proxy transport, auth, facade redaction |
| Retry, timeout, interruption, cleanup | `sites/retry.py`, `utils/retry_utils.py`, `runtime/runner.py`, `Chat.close`, `TimedGenerator` | retry, network-retry, runner, chat-model, live-service, timed-generator tests |
| Debug logging, redaction, debug sample capture | `debugging.py`, `redaction.py`, `debug_sample_utils.py` | debugging, redaction, debug-sample tests |
| Build, install, smoke, import boundaries | `Makefile`, `pyproject.toml`, GitHub Actions | `tests/test_makefile_contract_unit.py`, import-linter, release metadata, `make ci` |

## Preservation Rules

- Add or update a focused regression test before changing parser behavior,
  retry behavior, output persistence, runtime orchestration, or public API
  shape.
- Promote raw platform drift examples into `tests/fixtures/` before reshaping
  YouTube, Twitch, or Kick parser logic.
- Keep old public import paths working unless a deliberate compatibility break
  is documented and tested.
- Do not weaken coverage, import-linter, Any-density, module-size, complexity,
  build, smoke, or network-separation guardrails without documented rationale.
- Reopen closed maintenance deferrals only with new evidence, such as a third
  site, a failing capability, or an organic threshold breach during feature
  work.

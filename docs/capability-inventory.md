# Capability Inventory

Preservation checklist for behavior-preserving maintenance. Use this before
large refactors, parser reshaping, or tooling changes.

## Core Capabilities

| Capability | Implementation owner | Guardrails |
| --- | --- | --- |
| CLI parsing, generated help, logging/testing flags, headers | `cli.py`, `cli_args.py`, `models/` metadata | `tests/test_cli.py`, `tests/test_cli_help_unit.py`, `tests/test_cli_bridge_unit.py`, signal-handler tests |
| Python API: `ChatDownloader`, `run`, dataclasses, exports | `chat_downloader.py`, `models/`, package `__init__.py` files | model tests, facade tests, public API snapshots, facade-param sync |
| URL dispatch and site defaults | `runtime/site_dispatch.py`, `sites/base.py`, `models.SiteDefault` | site-dispatch, URL-matching, session tests, `SiteDefault` identity test |
| YouTube bootstrap, live/replay/clip continuations, parsing | `sites/youtube/` | YouTube unit tests, live-event fixtures, continuation fixtures, drift harness |
| Twitch live IRC | `sites/twitch/live_service.py`, `irc_transport.py`, `parsing/` | live-service, transport, IRC parser, drift harness tests |
| Twitch VOD and clip replay | `sites/twitch/replay_service.py`, `_replay_vod_loop.py`, `replay_transport.py` | replay service, replay transport, VOD edge tests |
| Twitch GraphQL hashes, badges, Client-ID | `graphql_client.py`, `constants.py`, `types.py`, `parsing/badges.py` | Twitch client, hash coverage, badge-purity tests |
| Kick live Pusher chat | `sites/kick/live_service.py`, `websocket_transport.py`, `api_client.py`, `parsing/` | Kick live-service, transport, api-client, parsing unit tests |
| Kick VOD replay | `sites/kick/replay_service.py` | Kick replay-service tests |
| Kick Pusher key discovery, Cloudflare handling, event/group maps | `sites/kick/constants.py`, `api_client.py`, `parsing/events.py` | Kick extractor, api-client, parsing-events tests |
| Output formats: JSONL, CSV, TXT; no JSON-array `.json` | `output/continuous_write.py`, `output/writers.py` | output writer, continuous-write, CSV injection, JSONL UTC, multiple-output tests |
| Filtering, formatting, time windows | `sites/filters.py`, `formatting/format.py`, `runtime/chat_pipeline.py` | filtering, formatting, chat-pipeline tests |
| Cookies, sessions, auth, proxy safety | `chat_downloader.py`, `runtime/session_lifecycle.py`, `sites/session.py`, YouTube auth | session, lifecycle, auth-client, facade redaction tests |
| Retry, timeout, interruption, cleanup | `sites/retry.py`, `utils/retry_utils.py`, `runtime/runner.py`, `TimedGenerator` | retry, network-retry, runner, timed-generator tests |
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

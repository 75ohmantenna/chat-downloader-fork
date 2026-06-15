# Architecture

High-level guide to `chat_downloader`'s module layout and layer contracts.
For day-to-day development conventions see [`AGENTS.md`](../AGENTS.md);
for deferred refactor decisions see
[`docs/maintenance-notes.md`](maintenance-notes.md).
For behavior-preservation coverage see
[`docs/capability-inventory.md`](capability-inventory.md).

---

## Layer diagram

```
┌──────────────────────────────────────────────────────────┐
│  cli.py / cli_args.py            (CLI entry point)       │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│  chat_downloader.py              (thin facade: run())     │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│  runtime/                        (orchestration)          │
│    cli_bridge · site_dispatch · chat_pipeline            │
│    runner · session_lifecycle · testing                  │
└──────┬──────────────────┬───────────────────────────┬────┘
       │                  │                           │
┌──────▼──────┐   ┌───────▼──────────────┐   ┌───────▼────┐
│  sites/     │   │  output/             │   │  format-   │
│  base · ...  │   │  ContinuousWriter   │   │  ting/     │
│  youtube/   │   │  writers.py          │   │            │
│  twitch/    │   └──────────────────────┘   └────────────┘
│  kick/      │
└──────┬──────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│  utils/                          (leaf; no upward deps)  │
│  models/                         (typed shapes only)     │
└─────────────────────────────────────────────────────────┘
```

### Layer rules (enforced by `lint-imports` in `make lint`)

| Contract | Rule |
|----------|------|
| `youtube` ⊥ `twitch` ⊥ `kick` | Independence — no site package imports another |
| `utils` is a leaf | No imports from `sites`, `runtime`, `output`, `formatting`, or `models` |
| `models` isolation | No imports from `runtime`, `output`, `cli`, `cli_args`, or `sites` |

---

## Package inventory

### Top-level
| Module | Purpose |
|--------|---------|
| `chat_downloader.py` | Thin facade; exposes `run()` and re-exports public types |
| `cli.py` | Argument parsing entry point (`main()`), signal handler, arg-parser builder |
| `cli_args.py` | Parser-construction machinery: `_ParamRegistrar`, all `_add_*_args` helpers, `splitter`/`parse_header`/`str2bool` converters |
| `debugging.py` | Logging setup (colorlog/plain handler), testing modes, colour detection |
| `redaction.py` | Token redaction (`sanitize_for_log`, `REDACTED`), opt-in debug-sample capture (`capture_debug_sample`) |
| `errors.py` | Public exception hierarchy |
| `metadata.py` | `__version__`, `__program__`, `__summary__` |
| `request_profiles.py` | Named HTTP request profiles (headers presets) |

### `models/`
| Module | Purpose |
|--------|---------|
| `_base.py` | Shared dataclass field helpers (`get_field_default`) |
| `_config.py` | `DownloaderConfig` (init-time settings) |
| `_request.py` | `ChatRequest` (per-request options) |
| `_runconfig.py` | `RunConfig`, `coerce_chat_request` |
| `_site_default.py` | `SiteDefault` marker for site-specific defaults |
| `__init__.py` | Single public surface for `from chat_downloader.models import …` |

### `runtime/`
| Module | Purpose |
|--------|---------|
| `cli_bridge.py` | Categorize `run()` kwargs into init / chat / run param groups |
| `site_dispatch.py` | URL → site resolution and site-specific defaults |
| `chat_pipeline.py` | Limits, timeouts, format, output routing |
| `runner.py` | Top-level run loop and cleanup |
| `session_lifecycle.py` | Cookie/session setup, cookie-domain validation |
| `testing.py` | Test-mode helpers |

### `output/`
| Module | Purpose |
|--------|---------|
| `continuous_write.py` | `ContinuousWriter` factory; re-exports writer types |
| `writers.py` | `ContinuousFileWriter` ABC; `CsvContinuousWriter`, `JsonLinesContinuousWriter`, `TextContinuousWriter`; `_WRITER_CLASSES` dispatch dict |
| `csv_rewrite.py` | In-place CSV column-addition helper |

### `sites/` (shared)
| Module | Purpose |
|--------|---------|
| `base.py` | `BaseChatDownloader` ABC: URL matching, session setup, cookie handling |
| `session.py` | `ChatDownloaderSession`: HTTP session, proxy config, auth |
| `retry.py` | `ChatDownloaderRetry`: retry policy with back-off |
| `filters.py` | Message-group validation and per-message filter application |
| `models.py` | `Chat`, `Image`; compatibility re-export of `models.SiteDefault` |
| `output_dispatch.py` | `ChatOutputWriter` Protocol, `_ChatOutputDispatcher`, `SUPERCHAT_DEDUP_TYPES` |
| `remap.py` | `Remapper`: field-rename and transform machinery |
| `_seen_cache.py` | `_SeenMessageCache`: bounded LRU dedup cache |
| `_protocols.py` | Shared Protocol definitions |

### `sites/youtube/`

#### `constants_*.py`
| Module | Purpose |
|--------|---------|
| `constants_actions_continuations.py` | Chat continuation token and action-wrapper key constants |
| `constants_actions_messages_core.py` | Core action-dict path keys for extracting message items |
| `constants_actions_messages_list.py` | Derived action-type lists assembled from the core constants |
| `constants_message.py` | Large remapping table for normalizing YouTube chat message fields |
| `constants_patterns.py` | URL patterns, API endpoint strings, and miscellaneous regex |

#### `client_*.py`
| Module | Purpose |
|--------|---------|
| `client_auth.py` | SAPISIDHASH authentication header generation |
| `client_context.py` | InnerTube context dict construction and request-profile application |
| `client_requests_bootstrap.py` | Fallback InnerTube bootstrap requests (initial video data) |
| `client_requests_continuation.py` | HTTP continuation polling: request dispatch, retry, error surfacing |
| `client_requests_errors.py` | HTTP/JSON error classification, captcha detection, retry helpers (Round-07) |
| `client_requests_initial.py` | Initial-page HTTP fetch and HTML/JSON extraction |

#### Chat-streams split (post-Round-02)
| Module | Purpose |
|--------|---------|
| `chat_streams.py` | `YouTubeChatStreamsMixin`; entry points for video and clip chat |
| `chat_streams_context.py` | Continuation-loop context construction (`_build_chat_context`, `_select_initial_continuation`, `_profiled_innertube_context`, `_apply_live_timing`) |
| `chat_streams_response.py` | Continuation response handling (`_handle_continuation_response`, `_raise_if_api_error`, `_update_visitor_data`, `_log_*` helpers) |
| `chat_streams_runtime_iteration.py` | The continuation loop itself (`_get_chat_messages`, `_process_actions`, `_advance_continuation_loop`, `_attempt_profile_fallback`) |

#### Other YouTube modules
| Module | Purpose |
|--------|---------|
| `_protocols.py` | YouTube-specific Protocol definitions |
| `chat_users_retrieval.py`, `chat_users_router.py` | Chat participant retrieval and routing |
| `continuation_loop.py`, `continuation_loop_runtime.py`, `continuation_loop_state.py`, `continuations.py` | Continuation state, token parsing, loop helpers |
| `discovery_channels_runtime_iteration.py`, `discovery_helpers.py`, `discovery_playlists.py` | Channel and playlist discovery helpers |
| `extractor.py` | YouTube site extractor class wiring mixins together |
| `helpers.py` | YouTube payload/navigation helpers |
| `message_pipeline.py` | Message filtering/remapping pipeline |
| `parsing/` | Action routing and message content parsers |
| `playability.py` | YouTube playability status classification |
| `video_initialization.py`, `video_metadata.py`, `video_status.py`, `video_status_helpers.py`, `video_status_models.py` | Video bootstrap metadata and status models |

### `sites/twitch/`
| Module | Purpose |
|--------|---------|
| `constants.py` | Client-ID and GraphQL operation hashes |
| `discovery.py` | Twitch URL discovery and GraphQL query construction |
| `extractor.py` | Twitch site extractor class |
| `graphql_client.py` | Persisted-query GraphQL client and error handling |
| `irc_transport.py` | Low-level IRC socket connection and message stream |
| `live_service.py` | Live IRC chat orchestration |
| `parsing/` | IRC message, tag, badge, and emote parsing |
| `remappings.py` | Twitch field remapping tables |
| `replay_service.py`, `_replay_vod_loop.py`, `replay_transport.py` | VOD/clip replay pagination and transport |
| `types.py` | Typed Twitch support containers |
| `url_generation.py` | Twitch URL builders |
| `validation_keys.py` | Known IRC tag/key validation lists |

### `sites/kick/`
| Module | Purpose |
|--------|---------|
| `extractor.py` | `KickChatDownloader` — URL matching, public API entry point |
| `live_service.py` | Live chat orchestration: channel metadata, chatroom resolution, message streaming with dedup and reconnect |
| `api_client.py` | Cloudflare-bypass HTTP client for Kick's unauthenticated `kick.com/api/v2` JSON endpoints; separate from the official OAuth-scoped `api.kick.com/public/v1` API |
| `websocket_transport.py` | Pusher WebSocket transport (framing/IO only); injectable for testing |
| `constants.py` | URL patterns, Pusher config, event names, message types, emote patterns, Cloudflare markers |
| `errors.py` | `KickError`, `KickServerError` |
| `parsing/events.py` | Pusher frame dispatch to typed event parsers |
| `parsing/messages.py` | Chat message normalization (text messages) |
| `parsing/emotes.py` | Inline emote marker parsing and structured metadata |
| `parsing/subscriptions.py` | Subscription and gifted-subscription event normalization |
| `parsing/moderation.py` | Ban, unban, message-delete, and chat-clear event normalization |
| `parsing/pins.py` | Pinned-message created/deleted event normalization |
| `parsing/hosts.py` | Stream-host event normalization |

---

## Guardrails (added round 2, 2026-06)

| Guardrail | Where |
|-----------|-------|
| Import-layering contracts | `pyproject.toml [tool.importlinter]`; enforced by `uv run lint-imports` (wired into `make lint`) |
| Public-API snapshot | `tests/test_public_api_unit.py` — frozen `__all__` sets for `chat_downloader` and `chat_downloader.models`; any intentional surface change must update the snapshot in the same commit |
| Module-size gate | `tests/test_module_size_unit.py` — 400-line ceiling on all source modules (allowlist for intentional data tables and cohesive modules); fails on future bloat |
| McCabe complexity | `ruff C9` rule, gate = 10; intrinsically branchy transport loops carry `# noqa: C901` with rationale |
| 100% line coverage | `pyproject.toml [tool.coverage.report]`; enforced by `make coverage` / `make ci` |
| Any-density ratchet | `tests/test_any_density_unit.py` — per-module baseline; lower as typing debt is paid; never raise |

Remaining targets and deferred decisions: [`docs/maintenance-backlog.md`](maintenance-backlog.md).

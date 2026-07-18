# Architecture

High-level guide to `chat_downloader`'s module layout and layer contracts.
For day-to-day development conventions see [`AGENTS.md`](../AGENTS.md);
for deferred refactor decisions see
[`docs/maintenance-decisions.md`](maintenance-decisions.md).
For behavior-preservation coverage see
[`docs/capability-inventory.md`](capability-inventory.md).

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
| Generic layers stay provider-neutral | `runtime`, `output`, and `formatting` must not import a concrete site package (`sites.youtube`/`twitch`/`kick`). The lone exception is the site registry `runtime.site_dispatch`, which imports the `sites` aggregate to enumerate downloaders. Provider-specific behavior (e.g. live-status classification, live-format overrides) lives behind capability methods on `BaseChatDownloader` (`is_live_status`, `resolve_live_format`) that sites override. |

## Network lifecycle

| Transport | Open/read path | Retry/reconnect owner | Close path |
|-----------|----------------|-----------------------|------------|
| Shared HTTP (YouTube and Twitch) | `sites/session.py` injects configured connect/read timeouts into every request | YouTube request modules and Twitch live/replay services classify retryable request/status failures | `BaseChatDownloader.close()`; closed cached site sessions are replaced rather than reused |
| Twitch live IRC | `twitch/irc_transport.py` opens TLS with the configured connect timeout, a one-second minimum receive poll, keepalive probes, and a 180-second idle watchdog | `twitch/live_service.py` reconnects with capped backoff and a consecutive-failure budget, reset after useful traffic | IRC `QUIT`/shutdown/close in the generator `finally` path |
| Kick API HTTP | `kick/http_session.py` creates the isolated Cloudflare-capable transport owned by `KickApiClient` | Kick live metadata and VOD pagination retry transient network, malformed-response, 429, and 5xx failures; the client classifies endpoint responses consistently | `KickChatDownloader.close()` closes the client and base sessions exactly once |
| Kick live WebSocket | `kick/websocket_transport.py` opens, subscribes, applies a one-second minimum receive poll, and treats 180 seconds without a decoded frame as stale | `kick/live_service.py` creates a fresh transport after bounded, backed-off consecutive failures, then deduplicates a recent-history backfill | Transport close in every setup-error, reconnect, generator-close, and normal-exit path |

`Chat.close()` propagates closure through message-limit and timeout wrappers to
the provider generator before output writers are finalized. This is the common
Ctrl-C/SIGTERM and early-stop cleanup path. Reconnect diagnostics are debug-only;
normal message output and file formats are unchanged.

JSONL and text writers flush each record, periodically sync the file descriptor,
and perform a final sync at close. JSONL append mode removes a malformed trailing
record (or adds a missing newline to a valid record) before writing new data;
text append mode also terminates an existing final line before appending. Output
path aliases resolving to the same file are attached only once.
Kick VOD reverse pagination spills to a temporary file after 1 MiB, keeping replay
memory bounded independently of the number of fetched messages.

---

## Package inventory

Each table catalogs a package's immediate Python modules. Subpackages such as
`parsing/` are summarized as one row. A contract test ensures every immediate
non-`__init__.py` module remains represented.

### Top-level
| Module | Purpose |
|--------|---------|
| `__main__.py` | `python -m chat_downloader` entry-point shim |
| `chat_downloader.py` | Thin facade; exposes `run()` and re-exports public types |
| `cli.py` | Argument parsing entry point (`main()`), signal handler, arg-parser builder |
| `cli_args.py` | Parser-construction machinery: `_ParamRegistrar`, all `_add_*_args` helpers, `splitter`/`parse_header`/`str2bool` converters |
| `debugging.py` | Logging setup (colorlog/plain handler), testing modes, colour detection |
| `redaction.py` | Token redaction (`sanitize_for_log`, `REDACTED`), opt-in debug-sample capture (`capture_debug_sample`) |
| `errors.py` | Public exception hierarchy |
| `metadata.py` | `__version__`, `__program__`, `__summary__` |
| `request_profiles.py` | Named HTTP request profiles (headers presets) |
| `debug_sample_utils.py` | Debug-sample naming and fixture-hint helpers |
| `_shared_defaults.py` | Leaf constant (`DEFAULT_MAX_SEEN_MESSAGE_IDS`) shared by runtime and site layers |
| `_timeout_defaults.py` | Leaf HTTP-timeout constants (`DEFAULT_CONNECT_TIMEOUT`, `DEFAULT_READ_TIMEOUT`) shared by models and session helpers |

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
| `_protocols.py` | Runtime-facing structural interfaces used to avoid import cycles |
| `cli_bridge.py` | Categorize `run()` kwargs into init / chat / run param groups |
| `site_dispatch.py` | URL → site resolution and site-specific defaults |
| `chat_pipeline.py` | Close-propagating limits, timeouts, format, output routing |
| `config_guards.py` | Proxy and cookie safety validation |
| `runner.py` | Top-level run loop and cleanup |
| `session_lifecycle.py` | Cookie/session setup, cookie-domain validation |
| `testing.py` | Test-mode helpers |

### `output/`
| Module | Purpose |
|--------|---------|
| `continuous_write.py` | `ContinuousWriter` factory; re-exports writer types |
| `writers.py` | `ContinuousFileWriter` ABC; `JsonLinesContinuousWriter`, `TextContinuousWriter`; `_WRITER_CLASSES` dispatch dict |

### `sites/` (shared)
| Module | Purpose |
|--------|---------|
| `base.py` | `BaseChatDownloader` ABC: URL matching, session setup, cookie handling |
| `common.py` | Stateless site utilities for key validation and mapped-key discovery |
| `session.py` | `ChatDownloaderSession`: HTTP session, proxy config, auth |
| `proxy.py` | Shared proxy resolution and TLS tunneling for live transports |
| `retry.py` | Shared retry and debug-only bounded reconnect back-off orchestration |
| `filters.py` | Message-group validation and per-message filter application |
| `models.py` | `Chat` (result model: metadata, iteration, close facade), `Image`; compatibility re-export of `models.SiteDefault`. Output/dedup are delegated to `_ChatOutputDispatcher` |
| `output_dispatch.py` | `ChatOutputWriter` Protocol and `_ChatOutputDispatcher`: writer setup, grouped raw/formatted item dispatch, and shutdown |
| `remap.py` | `Remapper`: field-rename and transform machinery |
| `_message_dedup.py` | Shared formatted-message policy: paid/ticker semantic deduplication for console and formatted files |
| `_seen_cache.py` | `_SeenMessageCache`: bounded FIFO dedup cache |
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
| `client_requests_errors.py` | HTTP/JSON error classification, captcha detection, and retry helpers |
| `client_requests_initial.py` | Initial-page HTTP fetch and HTML/JSON extraction |

#### Continuation loop
| Module | Purpose |
|--------|---------|
| `chat_streams.py` | `YouTubeChatStreamsMixin`; entry points for video and clip chat |
| `continuation.py` | The cohesive continuation loop: `_ContinuationLoop` owns setup (`_build_context`), response handling (`_handle_continuation_response`), and iteration (`run`) as methods; stateless composables (`_process_actions`, `_advance_continuation_loop`, `_raise_if_api_error`, `_profiled_innertube_context`) stay at module scope. `_get_chat_messages` is the factory the mixin calls |
| `continuation_helpers.py` | Pure, downloader-independent helpers: `ContinuationLoopState`, `build_continuation_params`, `update_state_from_result`, live-timing/poll-delay/URL/filter builders |
| `continuations.py` | Continuation response parser (`parse_continuation_response`, `summarize_continuation_payload`, `ContinuationParseResult`) |

#### Other YouTube modules
| Module | Purpose |
|--------|---------|
| `_protocols.py` | YouTube-specific Protocol definitions |
| `chat_users_retrieval.py`, `chat_users_router.py` | Chat participant retrieval and routing |
| `discovery.py` | `YouTubeDiscoveryMixin`: cohesive channel discovery, pagination, rendered-content traversal, and test URL generation |
| `discovery_playlists.py` | Playlist discovery and pagination |
| `extractor.py` | YouTube site extractor class wiring mixins together |
| `helpers.py` | YouTube payload/navigation helpers |
| `message_pipeline.py` | Message filtering/remapping pipeline |
| `parsing/` | Action routing and message content parsers |
| `playability.py` | YouTube playability status classification |
| `video_initialization.py`, `video_metadata.py`, `video_status.py`, `video_status_helpers.py`, `video_status_models.py` | Video bootstrap metadata and status models |

### `sites/twitch/`
| Module | Purpose |
|--------|---------|
| `_protocols.py` | Twitch transport and downloader structural interfaces |
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
| `replay_service.py` | VOD metadata, reverse pagination, time-window filtering, and chronological spooled output |
| `api_client.py` | Downloader-owned client and unified status/challenge/JSON policy for Kick channel, history, and VOD endpoints |
| `http_session.py` | Dedicated curl-cffi/cloudscraper/requests session construction and narrow transport Protocol |
| `pusher_discovery.py` | Pusher application-key discovery, cache ownership, and WebSocket URL construction |
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

## Guardrails

| Guardrail | Where |
|-----------|-------|
| Import-layering contracts | `pyproject.toml [tool.importlinter]`; enforced by `uv run lint-imports` (wired into `make lint`). Includes site independence, the `utils`/`models` leaf rules, and the provider-neutral contract keeping `runtime`/`output`/`formatting` free of concrete site packages |
| Public-API snapshot | `tests/test_public_api_unit.py` — frozen `__all__` sets for `chat_downloader` and `chat_downloader.models`; any intentional surface change must update the snapshot in the same commit |
| Module-size gate | `tests/test_module_size_unit.py` — 400-line ceiling on all source modules (allowlist for intentional data tables and cohesive modules); fails on future bloat |
| McCabe complexity | `ruff C9` rule, gate = 10; intrinsically branchy transport loops carry `# noqa: C901` with rationale |
| 100% line coverage | `pyproject.toml [tool.coverage.report]`; enforced by `make coverage` / `make ci` |
| Any-density ratchet | `tests/test_any_density_unit.py` — per-module baseline; lower as typing debt is paid; never raise |

Current targets: [`docs/maintenance-backlog.md`](maintenance-backlog.md).
Durable rationale: [`docs/maintenance-decisions.md`](maintenance-decisions.md).

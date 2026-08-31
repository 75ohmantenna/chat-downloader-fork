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
│  chat_downloader.py              (public facade)          │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│  runtime/                        (orchestration)          │
│    cli_bridge · site_dispatch · chat_pipeline            │
│    config_guards · runner · session_lifecycle            │
└──────┬──────────────────┬───────────────────────────┬────┘
       │                  │                           │
┌──────▼──────┐   ┌───────▼──────────────┐   ┌───────▼────┐
│  sites/     │   │  output/             │   │ formatting/│
│  base · ... │   │  ContinuousWriter    │   │ templates  │
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

### Core interfaces

| Interface | Responsibility |
| --- | --- |
| `ChatDownloader.get_chat_request()` | Stable typed facade for one retrieval request |
| `dispatch_chat()` | Resolve and normalize the URL, acquire the provider, resolve site defaults, and return a configured `Chat` |
| `configure_chat()` | Apply limits, timeouts, formatting, and output writers in lifecycle-safe order |
| `ChatDownloaderSession` | Own one provider's shared HTTP adapter and policy state |
| `_SiteSessionPool` | Own provider reuse, explicit-cookie propagation, replacement, and shutdown |

The stage helpers behind these interfaces are implementation details. Tests may
use narrow internal seams for adapter mechanics, but composition tests cross the
same interfaces as production callers.

## Network lifecycle

| Transport | Open/read path | Retry/reconnect owner | Close path |
|-----------|----------------|-----------------------|------------|
| Shared HTTP (YouTube and Twitch) | `ChatDownloaderSession` owns the requests adapter, headers, cookies, effective proxy state, and configured connect/read timeouts; `runtime/config_guards.py` rejects cookie authentication through remote explicit or environment proxies | YouTube request modules and Twitch live/replay services classify retryable request/status failures | `BaseChatDownloader.close()`; `_SiteSessionPool` replaces closed cached site sessions rather than reusing them |
| Twitch live IRC | `twitch/irc_transport.py` opens TLS with the configured connect timeout, a one-second minimum receive poll, keepalive probes, and a 180-second idle watchdog | `twitch/live_service.py` reconnects with capped backoff and a consecutive-failure budget, reset after useful traffic | IRC `QUIT`/shutdown/close in the generator `finally` path |
| Kick API HTTP | `kick/http_session.py` creates the Cloudflare-capable transports owned by `KickApiClient`, preserves explicit/environment proxy policy, and origin-isolates the anonymous mobile host from credential-shaped user headers and main-origin cookies | Kick channel metadata plus reconnect/VOD/clip history and metadata retry transient network, malformed-response, 429, and 5xx failures; provider-specific HTTP 423 is a terminal country/region block; clip replay can fail over from unavailable web/source-VOD metadata to the anonymous mobile v1 contract while preserving terminal access policy and reconciling known channel and duration evidence; startup/reconnect preload state remains one-shot best-effort | `KickChatDownloader.close()` closes the client sessions and base session exactly once |
| Kick live WebSocket | `kick/websocket_transport.py` opens, subscribes, applies a one-second minimum receive poll, and treats 180 seconds without a decoded frame as stale | `kick/live_service.py` creates a fresh transport after bounded, backed-off consecutive failures, waits for confirmed resubscription, recovers a ten-second timestamp baseline through a clock/latency-safe envelope under page/record limits, and permits one forced key-discovery reconnect before a repeated `pusher:error` becomes terminal | Transport close in every setup-error, reconnect, generator-close, and normal-exit path |

`Chat.close()` requests closure through message-limit and timeout wrappers before
output writers are finalized. When the timeout worker is actively advancing the
provider iterator, its join is intentionally bounded; writer finalization may
finish first, and the worker closes the provider iterator as soon as that active
advance returns or raises. Successful debug summaries expose
`prefetched_after_deadline_count` so provider source-emission counters can be
reconciled with records that crossed an overall or inactivity deadline and were
intentionally excluded from output. `deadline_prefetch_count_complete` is false
when bounded shutdown returns while the provider worker is still advancing; in
that case the count is explicitly a lower bound and may finish updating after
the summary. This is the common Ctrl-C/SIGTERM and early-stop cleanup path.
Reconnect diagnostics are debug-only; normal message output and file formats are
unchanged.

JSONL and text writers flush each record, periodically sync the file descriptor,
and perform a final sync at close. JSONL append mode removes a malformed trailing
record (or adds a missing newline to a valid record) before writing new data;
text append mode also terminates an existing final line before appending. Output
path aliases resolving to the same file are attached only once. Writer targets
are compared after `{title}`/`{id}` expansion; existing hard links are compared
by device and inode.
Kick VOD and clip replay pages forward from the selected start time, so
chronological output streams without buffering later history. Exact cursor
advancement is paired with bounded ID deduplication because Kick's visible
message timestamps have lower precision than its pagination cursors. A
first-page 400/422 validation body naming `start_time` can activate the prior
reverse/spooled compatibility path without converting unrelated client errors
into an expensive replay crawl.

---

## Package inventory

Each table catalogs a package's immediate implementation files. Subpackages
such as `parsing/` are summarized as one row. A contract test ensures every
immediate non-`__init__.py` Python module is represented and rejects stale
module names.

### Top-level
| Module | Purpose |
|--------|---------|
| `__main__.py` | `python -m chat_downloader` entry-point shim |
| `chat_downloader.py` | Thin public facade; delegates dispatch and session ownership, and exposes `run()` |
| `cli.py` | Argument parsing entry point (`main()`), signal handler, arg-parser builder |
| `cli_args.py` | Parser-construction machinery: `_ParamRegistrar`, all `_add_*_args` helpers, `splitter`/`parse_header`/`str2bool` converters |
| `debugging.py` | Logging setup (colorlog/plain handler), testing modes, color detection |
| `redaction.py` | Recursive secret and authentication-header redaction (`sanitize_for_log`, `REDACTED`), opt-in debug-sample capture (`capture_debug_sample`) |
| `errors.py` | Public exception hierarchy |
| `metadata.py` | `__version__`, `__program__`, `__summary__` |
| `request_profiles.py` | Canonical named HTTP request-profile presets used by validation and session setup |
| `debug_sample_utils.py` | Debug-sample naming and fixture-hint helpers |
| `_shared_defaults.py` | Leaf constant (`DEFAULT_MAX_SEEN_MESSAGE_IDS`) shared by runtime and site layers |
| `_timeout_defaults.py` | Leaf HTTP-timeout constants (`DEFAULT_CONNECT_TIMEOUT`, `DEFAULT_READ_TIMEOUT`) shared by models and session helpers |

### `models/`
| Module | Purpose |
|--------|---------|
| `_base.py` | Shared defaults, CLI metadata, and dataclass field helpers (`get_field_default`) |
| `_config.py` | `DownloaderConfig` (init-time settings) |
| `_request.py` | `ChatRequest` (per-request options) |
| `_runconfig.py` | `RunConfig`, `coerce_chat_request` |
| `_site_default.py` | `SiteDefault` marker for site-specific defaults |
| `__init__.py` | Single public surface for `from chat_downloader.models import …` |

### `runtime/`
| Module | Purpose |
|--------|---------|
| `cli_bridge.py` | Categorize `run()` kwargs into init / chat / run param groups |
| `site_dispatch.py` | `dispatch_chat`: HTTPS normalization (including protocol-relative inputs), site resolution, defaults, provider invocation, and configured-chat assembly |
| `chat_pipeline.py` | `configure_chat`: close-propagating limits, timeouts, formatting, expanded-output identity checks, and output routing |
| `config_guards.py` | Explicit/environment proxy and cookie-authentication safety validation |
| `runner.py` | Top-level run loop, structured `RunResult`, final diagnostics, testing-mode selection, and cleanup |
| `session_lifecycle.py` | `_SiteSessionPool`: site-instance cache, shared explicit cookies, replacement, and shutdown |

### `output/`
| Module | Purpose |
|--------|---------|
| `continuous_write.py` | `ContinuousWriter` factory; re-exports writer types |
| `writers.py` | `ContinuousFileWriter` ABC; `JsonLinesContinuousWriter`, `TextContinuousWriter`; `_WRITER_CLASSES` dispatch dict |

### `formatting/`
| Module | Purpose |
|--------|---------|
| `format.py` | `ItemFormatter`: safe template resolution, inheritance, field formatting, singular/conditional fragments, and output sanitization |
| `custom_formats.json` | Built-in default, provider-specific, live, and time-display format definitions |

### `utils/`
| Module | Purpose |
|--------|---------|
| `color_utils.py` | ARGB/RGBA color conversion |
| `console_utils.py` | Cross-platform safe console output and pause handling |
| `conversion_utils.py` | Scalar conversion, retry-attempt, and back-off helpers |
| `dict_utils.py` | Nested dictionary lookup, mutation, and first-item helpers |
| `filename_utils.py` | Safe single-component filename sanitization |
| `json_types.py` | JSON aliases plus typed accessors (`get_str`, `get_int`, `get_dict`, `get_list`, `dig`) |
| `json_utils.py` | JSON parsing, flattening, and nested update helpers |
| `retry_utils.py` | Immutable retry policy model |
| `string_utils.py` | Regex, wrapping, prefix/suffix, and name-normalization helpers |
| `time_utils.py` | Timestamp, duration, timezone, and ISO-8601 conversion |
| `timed_generator.py` | Close-propagating timeout and inactivity wrapper |
| `timed_input.py` | Interruptible console input with timeout support |

### `sites/` (shared)
| Module | Purpose |
|--------|---------|
| `base.py` | `BaseChatDownloader` ABC: URL matching, session setup, cookie handling |
| `common.py` | Stateless site utilities for key validation and mapped-key discovery |
| `session.py` | `ChatDownloaderSession`: cohesive HTTP adapter, timeout, proxy, header/profile, cookie, and close ownership |
| `proxy.py` | Shared proxy resolution and TLS tunneling for live transports |
| `retry.py` | Shared retry and debug-only bounded reconnect back-off orchestration |
| `filters.py` | Message-group validation and per-message filter application |
| `models.py` | `Chat` (result model: metadata, iteration, close facade), `Image`; compatibility re-export of `models.SiteDefault`. Output and deduplication are delegated to `_ChatOutputDispatcher` |
| `output_dispatch.py` | `ChatOutputWriter` Protocol and `_ChatOutputDispatcher`: safe `{title}`/`{id}` expansion, writer setup, grouped raw/formatted dispatch, completed-record and suppression counts, and shutdown |
| `remap.py` | `Remapper`: field-rename and transform machinery |
| `_message_dedup.py` | Shared formatted-message policy: paid/ticker semantic deduplication for console and formatted files |
| `_seen_cache.py` | `_SeenMessageCache`: bounded FIFO deduplication cache |

### `sites/youtube/`

#### `constants_*.py`
| Module | Purpose |
|--------|---------|
| `constants_actions_messages_core.py` | Core action-dict path keys for extracting message items |
| `constants_message.py` | Large remapping table for normalizing YouTube chat message fields |
| `constants_patterns.py` | URL patterns, API endpoint strings, and miscellaneous regex |

#### `client_*.py`
| Module | Purpose |
|--------|---------|
| `client_auth.py` | SAPISIDHASH authentication header generation |
| `client_context.py` | InnerTube context dict construction and request-profile application |
| `client_requests_bootstrap.py` | Fallback InnerTube bootstrap requests (initial video data) |
| `client_requests_continuation.py` | HTTP continuation polling: request dispatch, retry, error surfacing |
| `client_requests_errors.py` | HTTP/JSON error classification, CAPTCHA detection, and retry helpers |
| `client_requests_initial.py` | Initial-page HTTP fetch and HTML/JSON extraction |

#### Continuation loop
| Module | Purpose |
|--------|---------|
| `chat_streams.py` | `YouTubeChatStreamsMixin`; entry points for video and clip chat |
| `continuation.py` | The cohesive continuation loop: `_ContinuationLoop` owns setup (`_build_context`), response handling (`_handle_continuation_response`), and iteration (`run`) as methods; stateless composables (`_process_actions`, `_advance_continuation_loop`, `_raise_if_api_error`, `_profiled_innertube_context`) stay at module scope. `_get_chat_messages` is the factory the mixin calls |
| `continuation_helpers.py` | Pure, downloader-independent helpers: `ContinuationLoopState`, `build_continuation_params`, `update_state_from_result`, live-timing/poll-delay/URL/filter builders |
| `continuations.py` | Continuation token-key definitions and response parser (`parse_continuation_response`, `summarize_continuation_payload`, `ContinuationParseResult`) |

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
| `constants.py` | URL/IRC constants, Client-ID, GraphQL operations, and message group/remapping tables |
| `discovery.py` | Twitch URL discovery and GraphQL query construction |
| `extractor.py` | Twitch site extractor class |
| `graphql_client.py` | Persisted-query GraphQL client and error handling |
| `irc_diagnostics.py` | IRC control-traffic classification and bounded clean-run capture |
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
| `live_service.py` | Live chat orchestration: channel metadata, preloaded history and pin state, message streaming with deduplication, clock/latency-safe reconnect backfill, bounded diagnostics, and rejected-key recovery |
| `replay_service.py` | VOD metadata, replay-window and message filtering, chronological parsing, and reverse compatibility output |
| `clip_service.py` | Web/mobile clip metadata validation, clip-relative bounds, and source-VOD or absolute-time replay assembly |
| `history.py` | Timestamp-forward replay and reconnect pagination, exact microsecond cursor advancement, bounded ID deduplication, and loop guards |
| `request_retry.py` | Shared transient-request retry policy for Kick services |
| `api_client.py` | Downloader-owned, origin-scoped sessions and unified status/challenge/JSON policy for Kick channel, history, VOD, and web/mobile clip endpoints |
| `http_session.py` | Dedicated curl-cffi/cloudscraper/requests session construction and narrow transport Protocol |
| `pusher_discovery.py` | Default-first Pusher application-key selection, rejected-key refresh, cache ownership, and WebSocket URL construction |
| `websocket_transport.py` | Pusher WebSocket transport (framing/IO only); injectable for testing |
| `constants.py` | URL patterns, Pusher config, event names, message types, emote patterns, Cloudflare markers |
| `errors.py` | Terminal, transient, and validated history-fallback error classifications |
| `parsing/events.py` | Pusher frame dispatch to typed event parsers |
| `parsing/common_fields.py` | Shared scalar, timestamp, author, and badge normalization for Kick events |
| `parsing/messages.py` | Chat message normalization (text messages) |
| `parsing/emotes.py` | Inline emote marker parsing and structured metadata |
| `parsing/subscriptions.py` | Subscription and gifted-subscription event normalization |
| `parsing/moderation.py` | Ban, unban, message-delete, and chat-clear event normalization |
| `parsing/pins.py` | Pinned-message created/deleted event normalization |
| `parsing/polls.py` | Poll-update and poll-deleted state normalization |
| `parsing/hosts.py` | Stream-host event normalization |

---

## Guardrails

| Guardrail | Where |
|-----------|-------|
| Spelling | `pyproject.toml [tool.codespell]`; `make spell` checks every tracked file and filename, with exact raw-fixture lines recorded in `.codespell-ignore-lines` |
| Import-layering contracts | `pyproject.toml [tool.importlinter]`; enforced by `uv run lint-imports` (wired into `make lint`). Includes site independence, the `utils`/`models` leaf rules, and the provider-neutral contract keeping `runtime`/`output`/`formatting` free of concrete site packages |
| Public-API snapshot | `tests/test_public_api_unit.py` — frozen `__all__` sets for `chat_downloader`, `chat_downloader.models`, `chat_downloader.errors`, and `chat_downloader.sites`; any intentional surface change must update the snapshot in the same commit |
| Module-size gate | `tests/test_module_size_unit.py` — 400-line ceiling on all source modules (allowlist for intentional data tables and cohesive modules); fails on future bloat |
| McCabe complexity | `ruff C9` rule, gate = 10; intrinsically branchy transport loops carry `# noqa: C901` with rationale |
| 100% line coverage | `pyproject.toml [tool.coverage.report]`; enforced by `make coverage` / `make ci` |
| Any-density ratchet | `tests/test_any_density_unit.py` — per-module baseline; lower as typing debt is paid; never raise |

Current targets: [`docs/maintenance-backlog.md`](maintenance-backlog.md).
Durable rationale: [`docs/maintenance-decisions.md`](maintenance-decisions.md).

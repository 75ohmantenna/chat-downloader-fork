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
| Kick API HTTP | `kick/http_session.py` creates the isolated Cloudflare-capable transport owned by `KickApiClient` and preserves explicit/environment proxy policy | Kick live metadata and VOD pagination retry transient network, malformed-response, 429, and 5xx failures; the client classifies endpoint responses consistently | `KickChatDownloader.close()` closes the client and base sessions exactly once |
| Kick live WebSocket | `kick/websocket_transport.py` opens, subscribes, applies a one-second minimum receive poll, and treats 180 seconds without a decoded frame as stale | `kick/live_service.py` creates a fresh transport after bounded, backed-off consecutive failures; the first `pusher:error` forces key discovery and one reconnect, while a repeated error is terminal | Transport close in every setup-error, reconnect, generator-close, and normal-exit path |

`Chat.close()` propagates closure through message-limit and timeout wrappers to
the provider generator before output writers are finalized. This is the common
Ctrl-C/SIGTERM and early-stop cleanup path. Reconnect diagnostics are debug-only;
normal message output and file formats are unchanged.

JSONL and text writers flush each record, periodically sync the file descriptor,
and perform a final sync at close. JSONL append mode removes a malformed trailing
record (or adds a missing newline to a valid record) before writing new data;
text append mode also terminates an existing final line before appending. Output
path aliases resolving to the same file are attached only once. Writer targets
are compared after `{title}`/`{id}` expansion; existing hard links are compared
by device and inode.
Kick VOD reverse pagination spills to a temporary file after 1 MiB, keeping replay
memory bounded independently of the number of fetched messages.

---

## Package inventory

Each table catalogs a package's immediate Python modules. Subpackages such as
`parsing/` are summarized as one row. A contract test ensures every immediate
non-`__init__.py` module is represented and rejects stale module names.

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
| `site_dispatch.py` | `dispatch_chat`: HTTPS normalization (including protocol-relative inputs), site resolution, defaults, provider invocation, and configured-chat assembly |
| `chat_pipeline.py` | `configure_chat`: close-propagating limits, timeouts, formatting, expanded-output identity checks, and output routing |
| `config_guards.py` | Explicit/environment proxy and cookie-authentication safety validation |
| `runner.py` | Top-level run loop, testing-mode selection, and cleanup |
| `session_lifecycle.py` | `_SiteSessionPool`: site-instance cache, shared explicit cookies, replacement, and shutdown |

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
| `session.py` | `ChatDownloaderSession`: cohesive HTTP adapter, timeout, proxy, header/profile, cookie, and close ownership |
| `proxy.py` | Shared proxy resolution and TLS tunneling for live transports |
| `retry.py` | Shared retry and debug-only bounded reconnect back-off orchestration |
| `filters.py` | Message-group validation and per-message filter application |
| `models.py` | `Chat` (result model: metadata, iteration, close facade), `Image`; compatibility re-export of `models.SiteDefault`. Output and deduplication are delegated to `_ChatOutputDispatcher` |
| `output_dispatch.py` | `ChatOutputWriter` Protocol and `_ChatOutputDispatcher`: safe `{title}`/`{id}` expansion, writer setup, grouped raw/formatted item dispatch, and shutdown |
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
| `live_service.py` | Live chat orchestration: channel metadata, chatroom resolution, message streaming with deduplication, bounded reconnect, and rejected-key recovery |
| `replay_service.py` | VOD metadata, reverse pagination, time-window filtering, and chronological spooled output |
| `api_client.py` | Downloader-owned client and unified status/challenge/JSON policy for Kick channel, history, and VOD endpoints |
| `http_session.py` | Dedicated curl-cffi/cloudscraper/requests session construction and narrow transport Protocol |
| `pusher_discovery.py` | Default-first Pusher application-key selection, rejected-key refresh, cache ownership, and WebSocket URL construction |
| `websocket_transport.py` | Pusher WebSocket transport (framing/IO only); injectable for testing |
| `constants.py` | URL patterns, Pusher config, event names, message types, emote patterns, Cloudflare markers |
| `errors.py` | `KickError`, `KickServerError` |
| `parsing/events.py` | Pusher frame dispatch to typed event parsers |
| `parsing/common_fields.py` | Shared scalar, timestamp, author, and badge normalization for Kick events |
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
| Spelling | `pyproject.toml [tool.codespell]`; `make spell` checks every tracked file and filename, with exact raw-fixture lines recorded in `.codespell-ignore-lines` |
| Import-layering contracts | `pyproject.toml [tool.importlinter]`; enforced by `uv run lint-imports` (wired into `make lint`). Includes site independence, the `utils`/`models` leaf rules, and the provider-neutral contract keeping `runtime`/`output`/`formatting` free of concrete site packages |
| Public-API snapshot | `tests/test_public_api_unit.py` — frozen `__all__` sets for `chat_downloader`, `chat_downloader.models`, `chat_downloader.errors`, and `chat_downloader.sites`; any intentional surface change must update the snapshot in the same commit |
| Module-size gate | `tests/test_module_size_unit.py` — 400-line ceiling on all source modules (allowlist for intentional data tables and cohesive modules); fails on future bloat |
| McCabe complexity | `ruff C9` rule, gate = 10; intrinsically branchy transport loops carry `# noqa: C901` with rationale |
| 100% line coverage | `pyproject.toml [tool.coverage.report]`; enforced by `make coverage` / `make ci` |
| Any-density ratchet | `tests/test_any_density_unit.py` — per-module baseline; lower as typing debt is paid; never raise |

Current targets: [`docs/maintenance-backlog.md`](maintenance-backlog.md).
Durable rationale: [`docs/maintenance-decisions.md`](maintenance-decisions.md).

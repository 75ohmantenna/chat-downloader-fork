# Changelog

<!--
Keep entries user-observable or release-relevant. Internal refactors, test
churn, and documentation maintenance belong in Git history unless they change
behavior, compatibility, packaging, validation, or contributor workflow.
-->

## Unreleased

### Features

- Stream Kick VOD and clip replay chronologically from the requested start
  timestamp, allowing bounded and `max_messages` requests to stop without
  downloading later pages from the selected window.
- Backfill Kick WebSocket and Pusher-key reconnect gaps through timestamped
  history after confirmed resubscription, using a clock/latency-safe ten-second
  baseline and bounded page/record work while reconciling a time-filtered
  preload fallback and refreshed current pin state.
- Preserve Kick's image-backed modern sender badges in structured output
  alongside legacy role and subscription badges, including selection state and
  provider metadata.

## 2.1.0 — 2026-08-25

### Features

- Add bounded Kick clip chat replay through
  `kick.com/{channel}/clips/{clip_id}` URLs. Clip-relative time bounds are
  validated against clip metadata, translated onto the source VOD, and emitted
  chronologically through the existing spooled replay path.
- Accept YouTube channel, user, and handle `/live` shortcuts and resolve them
  to the active video before normal playability and chat initialization.
- Add an optional `youtube_replay_poll_interval` CLI and typed-API setting for
  explicitly bounded completed-replay polling.
- Extend `RunResult` with per-message-type counts while preserving the existing
  positional field layout.
- Add `singular_template` and `omit_if_false` custom-format field controls, and
  make Kick's event-aware text formatter the provider default.

### Fixes

- Retry generic initial YouTube `UNPLAYABLE` responses across request profiles
  while preserving explicit user-agent and custom-header overrides.
- Tolerate optional YouTube thumbnail and text-run shape drift without dropping
  otherwise valid messages.
- Align the default live socket receive timeout with the one-second minimum
  enforced by Twitch and Kick while continuing to clamp explicit lower values.
- Keep paired YouTube paid and ticker replay items aligned in the zero-offset
  preroll by retaining their signed nested display timing.
- Ignore malformed, non-finite, and negative YouTube replay-wrapper offsets and
  apply clip rebasing once so nested ticker timing remains usable.
- Continue across YouTube replay pages that remain just before `start_time`,
  preventing dense chats from ending before the requested range is reached.
- Preserve millisecond replay offsets when YouTube ticker items embed a paid
  message renderer with rounded display time, keeping paired JSONL records
  chronologically aligned.
- Stop advertising YouTube's transient `placeholder` message group and
  `placeholder_item` type, which the parser intentionally ignores and could
  never emit.
- Avoid scanning Kick's homepage and JavaScript bundles before every live-chat
  WebSocket connection; use the compiled public Pusher key first and reserve
  best-effort discovery for rejected-key recovery.
- Render YouTube moderation events without message text as informative notices
  instead of blank lines in text output.
- Render authorless YouTube live system events without a dangling author
  separator in the default, 24-hour, and 12-hour text formats.
- Preserve signed capture-relative timing for YouTube messages received from
  the initial live backlog while keeping continuation polling offsets
  nonnegative and monotonic.
- Retain Twitch badges when a replay comment omits its author object.
- Preserve Twitch raid and unraid system-event descriptions in text output,
  with safe raider/author fallbacks when optional fields are missing or empty.
- Preserve Twitch subscription-family and viewer-milestone descriptions while
  separating any appended user messages in text output.
- Render one-second Twitch timeouts with singular grammar in text output while
  retaining numeric timeout durations in structured output.
- Distinguish a Kick pin event's timestamp from the original chat message time
  through `metadata.original_message_created_at`, while retaining the previous
  metadata name as a compatibility alias.
- Preserve Kick's current pin state, reply moderation context, live receive-time
  fallback, and AI-moderation rule labels in normalized and formatted output.
- Render Kick subscription, pin, host, and moderation events as informative
  text notices instead of blank lines.
- Keep empty Kick VOD and clip windows empty without making an unnecessary
  history request.
- Close provider iterators after deferred timeout-worker completion so early
  limits and shutdown do not leave generators open.
- Report configured lazy output paths that were not created because a
  successful run retrieved no records.

### Security / hardening

- Redact YouTube continuation tokens and credential-shaped Google API keys
  from debug logs, including urllib3 request-target messages that log paths
  separately from origins.

### Debugging

- Add a separately opted-in, sanitized capture of the first three successfully
  parsed raw Twitch IRC frames, with one bound spanning reconnects.
- Add a separately opted-in, sanitized capture of the first three successfully
  parsed Kick WebSocket frames per normalized event type, with per-type bounds
  spanning reconnects.
- Add a separately opted-in, sanitized capture of the first three structurally
  valid YouTube continuation responses for clean-run diagnosis.
- Capture bounded, sanitized YouTube, Twitch, and Kick parser-drift samples,
  including raw provider context needed to promote a regression fixture.
- Distinguish processed YouTube actions from emitted messages in per-poll
  debug diagnostics and categorize non-emitting actions as known ignored
  controls or renderers, parser failures, message-filter exclusions, or
  time-range filtering outcomes.
- Report final retrieved-message, formatted-output suppression, and
  per-output-writer record counts after a successful debug run, including
  provider diagnostics and lazy-file creation state.
- Log Twitch's known optional `user.primaryTeam` GraphQL service error at debug
  level while retaining warnings for unfamiliar service-error paths in the
  same response.
- Capture sanitized Twitch drift samples for unknown IRC actions, message
  types, tags, unmatched lines, and unexpected replay shapes while retaining
  promotable raw payloads. Twitch captures are limited to ten unique samples
  per drift label and process to prevent unbounded diagnostic output.
- Report the effective Twitch IRC receive timeout after applying the one-second
  minimum.
- Report the effective Kick WebSocket receive timeout after applying the
  one-second minimum.
- Report decoded/control/event counts, reconnects, Pusher errors and key
  recoveries, and the last decoded-frame timestamp for Kick live runs.

### Performance

- Skip terminal YouTube waits and follow continuation tokens across empty
  replay pages while respecting provider delay hints by default.

## 2.0.7 — 2026-08-25

### Tooling

- Add codespell to the locked development toolchain, pre-commit checks, and
  canonical `make ci` validation. The spelling target checks every tracked
  file and filename while preserving exact third-party fixture text.
- Strengthen documentation contracts so code remains authoritative for typed
  field/default tables, CLI flags, output formats, provider message groups,
  and architecture module inventories.

## 2.0.6 — 2026-08-09

### Security / hardening

- Redact secrets embedded in quoted JSON and Python mapping strings before
  rendering project log records.
- Refuse debug-sample capture when secure directory-relative, no-follow file
  creation is unavailable instead of using a race-prone path fallback.
- Require successful 2xx responses for Kick Pusher-key discovery; redirect
  responses now fall back to the compiled key without parsing their bodies.

### Fixes

- Parse YouTube Jewels gift attribution widgets as
  `gift_message_view_model` events, preserving gifter identity, gift artwork,
  accessibility text, and combo count instead of reporting an unknown action.
- Skip incomplete Jewels attributions without emitting empty message identities,
  and omit optional fields that are absent from valid widgets.

### Tooling

- Replace Coverage's built-in ellipsis exclusion so bare ellipsis statements
  count toward the 100% line-coverage ratchet; structural protocol declarations
  remain explicitly exempt.

## 2.0.5 — 2026-08-09

### Security / hardening

- Sanitize project log records at the handler boundary, including URL
  credentials, sensitive query parameters, exception text, stack information,
  and terminal control characters.
- Create debug-sample directories and files with private permissions, reject
  unsafe ownership, modes, and symbolic links, and use atomic no-follow file
  creation where the platform supports it.
- Disable automatic redirects while discovering Kick's Pusher key, and avoid
  echoing potentially credential-bearing proxy URLs in validation errors.

### Fixes

- Make explicit `message_types` override `message_groups`, including the
  otherwise unrestricted `all` group.
- Reject site-session cache collisions between distinct downloader classes
  with the same class name.

### Tooling

- Fetch full Git history in hosted validation so the issue-reference guard can
  inspect every reachable commit.
- Expand `make clean` to remove all generated project artifacts, count ellipsis
  statements in coverage, and align documented commit subjects with the
  `topic: summary` convention.

## 2.0.4 — 2026-07-26

### Tooling

- Raise the `mypy` dev-dependency floor from `>=1.15,<2.0` to `>=2.3,<3.0`. The
  `<2.0` cap dated from the original `pyproject.toml` migration and carried no
  recorded rationale; mypy 2.3 type-checks the tree clean under the existing
  `strict = True` / `warn_unreachable = True` settings with no source changes.
  The pre-push hook runs `uv run --locked mypy .`, so it tracks the new major
  automatically
- Refresh `uv.lock` to current releases: `ruff` 0.15.15 → 0.16.0, `pytest`
  9.0.3 → 9.1.1, `coverage` 7.14.1 → 7.15.2, `import-linter` 2.11 → 2.13,
  `pre-commit` 4.6.0 → 4.6.1, `colorlog` 6.10.1 → 6.12.0, plus transitive
  updates
- Parenthesize an implicit string concatenation in `tests/test_runner_unit.py`
  flagged by ruff 0.16's `ISC004`; the two-part log message is unchanged

## 2.0.3 — 2026-07-23

### Fixes

- **Applied cookie-authentication safety checks to effective proxies.** Explicit
  proxies and proxies selected from the environment now follow the same policy;
  `proxy=""` remains the explicit opt-out from system proxy settings.
- **Accepted protocol-relative input URLs.** Inputs beginning with `//` are now
  normalized to HTTPS before site dispatch.
- **Recovered automatically from a rejected Kick Pusher key.** The live service
  performs one forced key discovery and reconnect before treating a repeated
  Pusher error as terminal.
- **Expanded debug-log redaction for custom authentication headers.** Header
  names containing authentication, token, secret, credential, or API-key
  markers, and standard authentication-scheme values, are now sanitized.
- **Deduplicated output aliases after metadata expansion.** Paths that become
  identical after `{title}` or `{id}` substitution, including existing hard
  links, attach only one writer.
- **Rejected unknown request profiles at configuration time.**
  `DownloaderConfig` now raises `ValueError` unless `request_profile` is one of
  the documented presets or `None`.

## 2.0.2 — 2026-07-20

### Architecture / compatibility

- **Deepened runtime orchestration without changing the documented API.**
  Chat dispatch and pipeline configuration now each expose one cohesive
  interface, while URL normalization, site defaults, wrapper ordering, and
  output setup remain internal. Undocumented orchestration helpers are no
  longer re-exported from `chat_downloader.runtime`; `RunResult` remains the
  stable runtime export.
- **Concentrated HTTP and provider-session ownership.**
  `ChatDownloaderSession` now owns shared HTTP state, and `_SiteSessionPool`
  owns provider reuse, explicit-cookie propagation, replacement, and shutdown.
  Broad owner-shaped protocols and forwarding helper clusters were removed.

## 2.0.1 — 2026-07-18

### Fixes

- **Restored YouTube chat capture with Android and iOS request profiles.**
  Mobile InnerTube layouts now provide both Top and Live chat continuations,
  and modern mobile text-message elements are normalized with author, badge,
  avatar, message ID, and timestamp metadata.
- **Kept YouTube InnerTube authentication and client metadata consistent.**
  Account session binding now uses YouTube's expected SAPISIDHASH suffix, and
  current web, Android, and iOS profile IDs, versions, bodies, and headers stay
  aligned during bootstrap and automatic fallback.
- **Prevented premature YouTube channel and playlist pagination stops.**
  Discovery now follows modern continuation view models, sends API headers on
  browse requests, and carries rotated visitor data into the next page.
- **Recognized Twitch social-sharing badge notices.** These USERNOTICE events
  now map to the existing badge message type instead of being reported as
  unknown IRC actions.
- **Clarified live receive-timeout behavior.** CLI help now explains that
  Twitch and Kick enforce a one-second minimum while still delivering messages
  immediately when data arrives.
- **Made Kick replay bounds and proxy failures actionable.** Kick VOD replay
  honors bounded `start_time` and `end_time` offsets, live channels reject
  unsupported seeking, and WebSocket proxy failures identify the proxy path.

## 2.0.0 — 2026-07-18

### Breaking changes

- **Removed CSV file output.** File output now requires a `.jsonl` or `.txt`
  path; CSV, unknown, and extensionless paths are rejected before file creation.

### Documentation / tooling

- Consolidated maintenance guidance into a current backlog and compact design
  decisions, retired completed refactor plans, corrected API and architecture
  drift, and added documentation link/API/module-inventory contract tests.

## 1.6.5 — 2026-06-28

### Reliability / hardening

- **Hardened unattended chat captures for multi-day operation.**
  Twitch IRC and Kick WebSocket sessions now detect stale connections, use
  bounded idle polling and reconnect backoff, and close transports reliably.
  Kick reconnects backfill recent retained messages with bounded duplicate
  suppression, while long Kick VOD replays spill page batches to disk instead
  of retaining the full replay in memory.
- **Made continuous output more durable and recoverable.**
  Writers now surface flush and sync failures, perform a final sync on close,
  repair interrupted JSONL tails before append, and close CSV rewrite handles
  on every path.

## 1.6.4 — 2026-06-27

### Changes

- **Emote names in plain-text messages are now colon-wrapped across services.**
  Kick named emotes render as `:PogU:` instead of bare `PogU`, and YouTube
  custom emoji that fall back to an emoji ID (no shortcuts) now render as
  `:emojiId:` instead of a bare ID. This aligns both services with YouTube's
  existing `:shortcut:` convention.

## 1.6.3 — 2026-06-21

### Security / hardening

- **Bound untrusted-data parsing and constrain Kick egress.**
  Tightened input validation on untrusted API payloads and restricted
  outbound connections for the Kick integration.

### Chores

- Fixed spelling findings across the codebase.

## 1.6.2 — 2026-06-21

### Fixes

- **Kick live channel preloaded history was emitted newest-first.**
  The `/channels/{id}/messages` API returns messages newest-first. The VOD
  replay path already reversed them, but the live path was yielding preloaded
  history in raw API order. Fixed by reversing the batch before emission so
  preloaded history appears in chronological order, consistent with all other
  sites and with the VOD path.

## 1.6.1 — 2026-06-17

### Typing / maintainability

- **Kick parsing layer narrowed off `Any` (Round-13).** Parser entry points in
  six `sites/kick/parsing/` modules take `object`/`Mapping[str, object]` params
  with `_opt_str`-style extraction; behavior unchanged.
- **Kick non-parsing layer migrated to `json_types` (Round-14).** HTTP-response
  boundaries (`api_client.py`) and the WebSocket-frame boundary
  (`websocket_transport.py`) use `JSONAny`/`JSONDict`/`JSONList` with `cast`;
  downstream callers in `live_service.py`/`replay_service.py` were
  param-narrowed accordingly. `api_client.py` retains `Any` only for the
  curl-cffi/cloudscraper impersonation session object.
- **Removed stale lint suppressions and a redundant import.**
  `kick/replay_service.py` and its test now use the already-imported
  `datetime.UTC` sentinel instead of `timezone.utc`, dropping three
  `# noqa: UP017` markers (the suppression rationale was false) and an unused
  `timezone` import. Fixed a misplaced `#:` doc-comment in `_timeout_defaults.py`.

### Docs

- Synced the architecture.md top-level module table with the source tree
  (`debug_sample_utils.py`, `_shared_defaults.py`, `_timeout_defaults.py`).

### Tests

- Added a contract test asserting every non-dunder top-level module appears in
  the architecture.md Top-level table, freezing the corrected inventory against
  future drift.

## 1.6.0 — 2026-06-16

### Features

- **curl-cffi integration:** Replaced cloudscraper as the primary HTTP backend
  for Kick REST API. Uses Chrome 124 TLS impersonation (`curl-cffi`) which
  bypasses Cloudflare challenges at the TLS-fingerprint level before they're
  even presented. Falls back to cloudscraper (JS-challenge solver), then plain
  requests.Session.

### Fixes

- Updated `CaptchaChallengeRequired` error message in `_raise_for_challenge()`
  to truthfully reflect that curl-cffi and cloudscraper were both attempted,
  instead of the misleading "this implementation does not bypass challenges".

### Docs

- Updated Cloudflare Dependency section in `docs/kick-integration-guide.md`
  to document the three-tier session strategy (curl-cffi → cloudscraper →
  requests.Session).

## 1.5.0 — 2026-06-15

### Chores

- Removed `.hermes/` directory from git tracking and added to `.gitignore`.

### Docs

- Trimmed README: removed local-checkout reinstall and day-to-day development
  sections; Documentation section already links to all three platform
  implementation guides (YouTube, Twitch, Kick).

## 1.4.0 — 2026-06-15

### Fixes

- **[critical] Kick HTTP + WebSocket now honor user transport config (proxy, cookies, headers, timeouts).** `_get_kick_session()` accepts optional `proxy` and `extra_headers` kwargs; `KickPusherTransport` threads `http_proxy_host`/`http_proxy_port` through its connector to `create_connection()`. Proxy/config flows from the downloader's configured session through Kick's API calls and WebSocket transport. Previously `--proxy`, `--cookies`, `--headers`, and timeouts were silently ignored for Kick — the challenge error message advised proxy changes that had no effect.
- **[critical] YouTube unknown continuation types now extract generic tokens instead of silently truncating chat.** `_extract_next_continuation()` in `continuations.py` previously returned `(None, ..., {unknown: True})` for unrecognized continuation wrappers, causing `parse_continuation_response()` to set `is_end=True` and exit normally — silently discarding any further messages behind the new wrapper. Now the generic `continuation` field is extracted from unknown entries, keeping the stream alive. The `unknown: True` flag in `debug_info` preserves observability.
- **[critical] Output write/close errors no longer silently reported as success.** `_ChatOutputDispatcher` tracks write-close errors via `_write_error_count`; `_finalize_run` in `runner.py` checks this after close and raises `ChatDownloaderError` when any writer failed and no primary error existed; `execute_run` catches this and sets `result.success = False` with an informative error message.
- **[high] Kick VOD `max_messages` now returns oldest N messages (first N of the VOD) instead of newest N.** Removed the premature `max_messages` early-break during pagination — the loop now paginates fully through the time window before reversing to chronological order and slicing. The old behavior broke out after collecting N newest messages, yielding the most recent N instead of the first N from the stream start.
- **[high] Kick VOD message timestamp normalization.** `_classify_message()` now normalizes naive `msg_dt` to UTC-aware before comparing with the VOD window, preventing a `TypeError` crash if Kick returns timestamps without timezone info.
- **[medium] Removed unreachable `rewards`/`reward_redeemed` message group.** `REWARD_REDEEMED_EVENT` and its mappings in `EVENT_NAME_MAP`, `MESSAGE_GROUPS`, and `MESSAGE_TYPE_REMAPPING` are removed. No parser existed for `reward_redeemed`, so selecting `--message_groups rewards` silently returned nothing. Re-add with a proper parser when needed.
- **[medium] Removed unused `nodriver` dependency** from `pyproject.toml`. It was never imported in any source or test file.
- **[low] Kick VOD pagination cursor now passed as a query parameter** via `session.get(url, params={...})` instead of string interpolation, fixing potential encoding issues with reserved characters.
- **[low] Cloudflare body detection tightened** — the secondary check now requires both `text/html` content-type AND an HTML-looking body document, reducing false positives on plain HTML error pages returned by the API.
- **[low] Pusher-key discovery scan limit reduced** from 30 to 15 JS bundles for faster startup.
- **[low] Debug logging no longer dumps full HTML pages.** `client_requests_initial.py` now logs a bounded 500-char summary plus length on parse failure, instead of the full HTML body.
- **[low] Extended redaction coverage.** `_SENSITIVE_LOG_KEYS` now includes `id_token` and `x-youtube-identity-token` so these YouTube auth headers are redacted from debug samples.
- **[low] Removed stale dead names `PUSHER_WS_URL` and `PUSHER_APP_KEY`** from Kick constants. These were computed at import time with the static default key and never imported by any consumer — `get_pusher_ws_url()`/`resolve_pusher_key()` are the canonical paths.
- **[low] Removed duplicate inner `import re`** inside `resolve_pusher_key()` (already at module scope).
- **[low] VOD replay docstring updated** to clarify that messages are collected into memory before yielding (bounded by 500-page ceiling), with `max_messages` returning oldest N.

## 1.3.0 — 2026-06-15

### Kick integration

- **New site: Kick.com chat support.** Unauthenticated, read-only chat
  retrieval for live channels (`kick.com/{username}`) via Kick's public Pusher
  WebSocket, and VOD chat replay (`kick.com/{username}/videos/{uuid}`) via the
  REST message history API. Includes Cloudflare-bypass using `cloudscraper` for
  the REST API lookups.
- **VOD chat replay:** Past-broadcast chat is reconstructed by paginating the
  channel message history and filtering to the VOD's time window, yielded in
  chronological order.
- **Full event coverage:** `ChatMessageEvent` (text), `SubscriptionEvent` (new
  subscribers), `GiftedSubscriptionsEvent` (gifted subs), `UserBannedEvent`,
  `UserUnbannedEvent`, `MessageDeletedEvent`, `PinnedMessageCreatedEvent`,
  `PinnedMessageDeletedEvent`, `StreamHostEvent`, `ChatClearMessagesEvent`.
- **Message groups:** `"messages"`, `"subscriptions"`, `"moderation"`,
  `"pins"`, `"hosts"` — use e.g. `--message_groups subscriptions moderation`
  to capture non-text event types.
- **Cloudscraper integration:** Kick REST API requests use `cloudscraper` for
  automatic Cloudflare JS challenge handling, falling back to a standard
  `requests.Session` with Kick-specific headers when cloudscraper is unavailable.
- **Preloaded history:** Recent messages fetched on connect and deduplicated
  against the live feed.
- **Auto-discovered Pusher key:** The Pusher application key is discovered from
  Kick's public JS bundle at runtime, falling back to a compiled-in default.
- **Public API:** `KickChatDownloader` and `KickError` are exported from the
  top-level `chat_downloader` package and included in `get_all_sites()`.
- **Offline test suite:** 154 Kick-specific tests, all offline, fixtures at
  `tests/fixtures/kick/`.

### Dependencies

- `websocket-client>=1.7.0,<2.0.0` — synchronous Pusher WebSocket transport.
- `cloudscraper>=1.2.0,<2.0.0` — Cloudflare JS challenge bypass for REST API.

## 1.2.0 — 2026-06-12

### API changes (minor, backward-compatible for keyword callers)

- `ChatDownloader.__init__`, `ChatDownloader.get_chat`,
  `ChatDownloader.set_cookie_value`, `ChatDownloader.create_session`,
  `ChatDownloader.run`: boolean and option params are now keyword-only.
  `url` in `get_chat` remains positional; all other params require keyword
  syntax. Positional-boolean callers must add argument names (Round-10.5).
- `BaseChatDownloader.set_cookie_value`, `BaseChatDownloader.retry`:
  keyword-only for optional params following the last required arg.
- `ContinuousWriter`, `ContinuousFileWriter` and subclasses: `overwrite`,
  `sort_keys`, `flush`, `flatten` params keyword-only.
- `Remapper.__init__`: `to_unpack` keyword-only.
- Internal helpers across `debugging`, `sites/retry`, `sites/remap`,
  `utils/time_utils`, `utils/timed_input`, `utils/console_utils`,
  `utils/json_types`, `utils/retry_utils`: boolean params keyword-only.

### Tooling

- Line length widened 80 → 88 (ruff default); `.git-blame-ignore-revs` added
  for the reformat commit so `git blame` skips it (Round-10.1)
- McCabe complexity gate raised 8 → 10 (forward-friction reduction; no
  existing noqa removed) (Round-10.2)
- Coverage pragma policy formalized; `_log_player_response_shape` (debug-only
  logging helper) marked `# pragma: no cover`; 4 branch-coverage-only tests
  removed (Round-10.3)
- Any-density ratchet retired as a scheduled activity; baselines frozen as a
  non-regression floor (Round-10.4)
- `FBT` (flake8-boolean-trap) enabled in ruff select; suppressed in tests via
  per-file-ignores; one `# noqa: FBT001` in `cli_args.str2bool` (argparse
  converter) (Round-10.5)

## 1.1.0 — 2026-06-11

### Internal / structural

- Extract `_ChatOutputDispatcher`, `ChatOutputWriter` Protocol, and
  `SUPERCHAT_DEDUP_TYPES` from `sites/models.py` into new
  `sites/output_dispatch.py`; forward-ref cycle eliminated via `_ChatHost`
  structural Protocol; no public API change (Round-04.1)
- Extract token redaction and debug-sample capture (`REDACTED`,
  `sanitize_for_log`, `capture_debug_sample`) from `debugging.py` into new
  `redaction.py`; `supports_colour` stays with the handler it serves;
  no public API change (Round-04.2)
- Lower `Any`-density baselines: `sites/models.py` 20→13, `debugging.py` 8→3;
  new modules (`sites/output_dispatch.py`, `redaction.py`) inherit moved
  boundaries
- Split `utils/timed_utils.py` (422 LOC) into `utils/timed_input.py`
  (console-input concern) and `utils/timed_generator.py` (generator-timeout
  concern); both under 400 LOC; removed from module-size ALLOWLIST (Round-05.1)
- Extract `_VodLoopPlan`, `_init_vod_loop`, `_classify_empty_page` from
  `sites/twitch/replay_service.py` into new `sites/twitch/_replay_vod_loop.py`;
  `replay_service.py` drops from 377 → 353 LOC (Round-05.2)
- Extract `_recover_incomplete_continuation` from `_get_chat_messages` in
  `sites/youtube/chat_streams_runtime_iteration.py`; reduces the
  `IncompleteContinuationError` recovery arm from ~8 lines to one call (Round-05.3)
- Extract error/retry helper cluster from
  `sites/youtube/client_requests_continuation.py` (367 LOC) into new
  `sites/youtube/client_requests_errors.py` (~245 LOC);
  `client_requests_continuation.py` drops to ~120 LOC (orchestration only);
  no public API change (Round-07.1)
- Land the deferred Round-06.2 extraction: `_handle_missing_live_chat_continuation` moves
  the missing-continuation guard out of `_get_continuation_info`
  (McCabe 8 → ~6); `dict[str,Any]` param paid in the new errors module
  (Round-07.1, lands the Round-06.2 deferral)
- Lower `Any`-density baseline `client_requests_continuation.py` 8 → 6;
  new `client_requests_errors.py: 4` (Round-07.1)
- Expand ruff lint rule set (Round-08.1): add N, EM, S, TRY (TRY003 ignored), PERF, G,
  BLE, PLW, ARG, A, RSE, PGH, ISC, FLY, INT, PLE, DTZ, PT families; fix ~80
  violations across src and tests; decline FBT (109) and SLF (53) with
  documented rationale; PGH now enforces code-specific `# noqa` annotations
- Add seam unit tests (Round-08.2): `test_youtube_client_requests_errors_unit.py`
  (HTTP/JSON error-handler cluster) and `test_twitch_replay_vod_loop_unit.py`
  (`_VodLoopPlan`, `_init_vod_loop`, `_classify_empty_page`); pin the Round-05/07
  extraction surfaces with direct unit tests

---

## 1.0.7 — 2026-06-10

### Tooling

- Tighten module-size gate `MAX_LINES` 450 → 400; add `utils/timed_utils.py`
  and `chat_downloader.py` to the allowlist as intentionally cohesive modules
  over 400 LOC; four 360–399-LOC modules rely on the headroom without allowlisting
- Add facade param-sync drift test (`tests/test_facade_param_sync_unit.py`):
  pins `ChatDownloader.get_chat()` parameter names, defaults, and docstring
  coverage against `ChatRequest` so the two cannot silently diverge
- Add `Any`-density ratchet gate (`tests/test_any_density_unit.py`): caps
  per-module `Any` occurrence counts at their post-round-3 baseline;
  `DEFAULT_CAP = 2` for uncapped modules; lowers automatically as debt is paid
  off, never raised

- Enforce `from __future__ import annotations` in every source file via ruff
  rule `I002` (`required-imports`); auto-applied repo-wide; moves type-only
  stdlib/application imports into `if TYPE_CHECKING:` blocks; adds
  `src/chat_downloader/sites/_protocols.py` to the coverage omit list (no
  longer imported at runtime)
- Add `.pre-commit-config.yaml`: `ruff check` and `ruff format --check` run on
  pre-commit; `mypy .` runs on pre-push; all hooks use `uv run --locked` so
  they always track the pinned `uv.lock` versions
- `make setup` now installs Git hooks in one step via `setup-hooks`
- Lower mccabe `max-complexity` from 10 to 8 in `pyproject.toml`; the CI `lint`
  step now enforces this directly; remove the redundant advisory `make
  complexity` target
- `mypy.ini`: narrow the `exclude` pattern to check `tests/fixtures/` and the
  two drift-regression harness modules in addition to all of `src/`

### Architecture

- Add `utils/json_types` leaf module: PEP 695 recursive `JSONScalar` /
  `JSONList` / `JSONDict` / `JSONAny` type aliases plus narrowing accessors
  (`get_str`, `get_int`, `get_float`, `get_bool`, `get_dict`, `get_list`,
  `dig`) that accept `Mapping[str, object]` and return concrete types;
  migrate highest-density payload modules (`actions_handlers_parser.py`,
  `format.py`, `playability.py`, `message_irc_resolve.py`) to the new
  accessors; wipe `Any` from these payload boundaries under strict mypy
- Tighten internal `Any`: `_SiteValueResolver` Protocol in `models/_request.py`
  replaces `site_object: Any`; `__exit__` context-manager params typed to
  `type[BaseException] | None` / `BaseException | None` / `TracebackType | None`

- Reduce cyclomatic complexity of 12 functions that measured 9–10 (above the
  new gate of 8) by extracting small named helpers:
  - `cli.py`: `_ParamRegistrar` class + per-group helper functions replace
    nested closures in `_build_arg_parser`
  - `sites/remap.py`: `Remapper._apply_remapper` extracted from `Remapper.remap`
  - `sites/twitch/discovery.py`: `_build_user_videos_query` and
    `_extract_user_videos` extracted from `get_user_videos`
  - `sites/twitch/irc_transport.py`: `_drain_readbuffer` and `_handle_ping`
    extracted from `get_chat_messages_by_stream_id`
  - `sites/youtube/client_auth.py`: `_ensure_primary_sapisid` and
    `_session_id_parts` extracted from `_generate_sapisidhash_header`
  - `sites/youtube/discovery_channels_runtime_iteration.py`:
    `_fetch_browse_continuation` extracted; first-page special case unrolled
    before the continuation loop in `get_user_videos`
  - `sites/youtube/parsing/actions_handlers_validation.py`:
    `_emit_parse_diagnostics` and `_derive_message_type` extracted from
    `validate_and_finalize_message`
  - `sites/youtube/parsing/message_content_badges.py`: `_parse_badge_icons`
    extracted from `_parse_badges`
  - `sites/youtube/parsing/message_content_text_parser.py`: `_append_run`
    extracted from `_parse_runs`
  - `sites/youtube/parsing/message_items_content_parser.py`:
    `_apply_colour_keys` and `_merge_nested_renderers` extracted from
    `_parse_item`
  - `sites/youtube/playability.py`: `_raise_for_early_playability` and
    `_raise_for_status` extracted from `_raise_for_error_screen`
  - `utils/timed_utils.py`: `_handle_error_result` and `_handle_item_result`
    extracted from `TimedGenerator.__next__`
- Convert `models.py` (592 lines) into a `models/` package; three dataclasses
  split into `_config.py`, `_request.py`, and `_runconfig.py` with a thin
  `__init__.py` facade; all `from chat_downloader.models import ...` call
  sites unchanged
- Split `sites/twitch/parsing/messages.py` (687 lines) into three focused
  modules: `message_emotes.py` (emote and image helpers), `message_irc_resolve.py`
  (IRC type/action/room-state resolution), and `messages.py` (orchestration
  entry points); public re-exports from `parsing/__init__.py` unchanged

### Architecture (round 4)

- Remove dead `BaseChatDownloader._move_to_dict` class-level alias in
  `sites/base.py`; no call sites used it (all callers import
  `move_to_dict` directly from `utils/dict_utils`); also removes the
  now-orphaned `from chat_downloader.utils.dict_utils import move_to_dict as
  _move_to_dict` import line
- Add `_ContinuationProgress` dataclass in
  `sites/youtube/chat_streams_runtime_iteration`: encapsulates the fallback-count
  and empty-poll-count bookkeeping from `_get_chat_messages`; replaces four
  local variables with two methods (`register_fallback`, `register_poll`);
  the noqa comment is retained but the rationale is updated to "intrinsically
  branchy" (structural complexity verified at 13, not a suppressible smell)
- Complete `runtime/_protocols.ChatDownloaderProto`: add `close()` method so
  the protocol covers all methods the runtime layer calls; apply it in
  `runtime/runner._finalize_run` (`chat: Chat | None`, `downloader:
  ChatDownloaderProto | None`) and `runtime/session_lifecycle.create_session`
  (`chat_downloader_class: type[BaseChatDownloader]`); lower per-module `Any`
  baselines in `tests/test_any_density_unit.py` accordingly

### Documentation (round 4)

- Correct `docs/architecture.md` guardrails table: module-size gate ceiling
  was still listed as 450 lines; update to 400 to match the actual gate in
  `tests/test_module_size_unit.py` and `docs/maintenance-notes.md`

### Documentation

- Add `docs/maintenance-notes.md`: records three intentionally deferred
  cross-site deduplication opportunities (remapping tables, badge parsing,
  continuation error-recovery) with rationale for deferral and future
  abstraction sketches; linked from `docs/development-workflow-guide.md`

### Testing

- Add `tests/test_twitch_drift_harness_unit.py`: fixture-parametrized replay
  harness for Twitch IRC parsing, mirroring the YouTube harness; seeds six
  curated IRC fixtures in `tests/fixtures/twitch/live_events/`
- Add offline GraphQL hash-rotation guard: asserts every `operationName` used
  in the Twitch client has an entry in `OPERATION_HASHES` (and vice versa)

### Architecture (round 2)

- Split `sites/youtube/chat_streams_runtime_iteration.py` (578 LOC) into
  three focused modules: `chat_streams_context.py` (continuation-loop context
  construction), `chat_streams_response.py` (response handling and error
  surfacing), and the continuation loop remainder in
  `chat_streams_runtime_iteration.py`
- Split `cli.py` (460 LOC): extract argument-building machinery
  (`_ParamRegistrar`, `_add_*_args`, `splitter`, `parse_header`, `str2bool`,
  `_build_request_headers`, `_build_field_info`) into new `cli_args.py`; `cli.py`
  retains only `_install_cli_signal_handlers`, `_build_arg_parser`, and `main`
- Split `output/continuous_write.py` (422 LOC): move `ContinuousFileWriter` ABC
  and the three concrete writer subclasses (`CsvContinuousWriter`,
  `JsonLinesContinuousWriter`, `TextContinuousWriter`) plus `_WRITER_CLASSES`
  into new `output/writers.py`; `continuous_write.py` retains the `ContinuousWriter`
  factory and re-exports the moved types via `__all__`

### Tooling (round 2)

- Add `import-linter` as a dev dependency; wire `uv run lint-imports` into
  `make lint` (and therefore `make ci`); three contracts enforced:
  `youtube ⊥ twitch` independence, `utils` is a leaf, `models` isolation from
  runtime/output/cli/site-packages
- Add `tests/test_public_api_unit.py`: frozen-set snapshot of
  `chat_downloader.__all__` and `chat_downloader.models.__all__`; intentional
  surface changes require updating the snapshot in the same commit
- Add `tests/test_module_size_unit.py`: 450-line ceiling on all source modules
  (with allowlist for intentional data tables); locks in round-2 split gains

### Documentation (round 2)

- Add `docs/architecture.md`: layer diagram, import-contract table, full module
  inventory for all packages including YouTube `constants_*`/`client_*` modules
  previously undocumented; linked from `AGENTS.md`
- Update `AGENTS.md` Structure and Architecture sections to reflect the new
  module paths and the import-linter guardrails
- Update `docs/maintenance-notes.md`: record the round-2 splits, guardrails,
  and intentionally over-budget modules with deferral rationale

### Fixed

- Recognize additional YouTube live-chat product, automod, and restricted
  participation renderers observed in YouTube.js coverage.

## 1.0.6 — 2026-06-03

### Build and tooling

- Remove non-standard paragraph from LICENSE so GitHub's licensee detector
  matches the canonical MIT template

## 1.0.5 — 2026-06-01

### Build and tooling

- Add Python 3.14 support while preserving 3.12 and 3.13; validate all three
  versions in GitHub and Gitea CI
- Add `.python-versions` for the uv-managed local validation matrix
- Require `CHANGELOG.md` updates alongside every version bump; the latest
  numbered release heading must match `src/chat_downloader/metadata.py::__version__`
- Add automated offline test (`tests/test_release_metadata_unit.py`) that
  fails if the current package version is not the topmost numbered changelog release

### Documentation

- Add version-bump checklist to the development workflow guide
- Repair missing 1.0.4 changelog coverage

## 1.0.4 — 2026-06-01

### Build and tooling

- Adopt uv for locking, dependency sync, developer commands, and builds;
  add `uv.lock` and `.python-version` for reproducible environments
- Replace pip/venv targets in `Makefile` with `uv run` equivalents
- Convert GitHub and Gitea CI workflows to install uv, cache the uv store,
  run checks on chore branches, and scope the uv cache key per Python version
- setuptools remains the PEP 517 build backend; uv manages the project

### Architecture

- Move package from flat `chat_downloader/` layout to `src/chat_downloader/`
  (src layout); update `pyproject.toml`, `mypy.ini`, CI, and tooling paths
  accordingly

### Documentation

- Align `AGENTS.md`, `CLAUDE.md`, and developer docs with uv commands and
  src layout paths
- Surface `uv tool install` before pipx in the `README.md` Installation section;
  fix `AGENTS.md` architecture section to match actual directory structure

## 1.0.3 — 2026-06-01

### Build and tooling

- Rewrite Ruff configuration; trim redundant pyproject.toml fields
- Add Makefile with `.venv`-scoped targets; expand doc-update policy in
  `AGENTS.md` to require changelog entries for user-visible changes
- Fix D205 docstring summaries and replace manual `try/except` blocks with
  `contextlib.suppress` throughout `src`
- Apply same D205 and `contextlib.suppress` fixes to the test suite; move
  `pytestmark` assignments to module scope (E402) and mark long lines with
  `noqa: E501`
- Parameterize bare `Callable` generics, fix bare collection generics, and
  add `cast` calls required by `mypy --strict`
- Credit fork maintainer in project metadata

## 1.0.2 — 2026-05-29

### Security

- Restrict the loopback-proxy exemption for cookie authentication to genuine
  loopback hosts (validated via `ipaddress`); spoofed names such as
  `127.0.0.1.attacker.com` are no longer treated as local and now raise
  `InvalidParameter`

### Fixed

- Parse RFC 3339 timestamps with comma decimal separators and explicit
  `+hh:mm`/`-hh:mm` offsets in `timestamp_to_microseconds`; previously only `Z`
  and dot-separated fractions were handled correctly

## 1.0.1 — 2026-05-29

### Output

- Close the CSV file handle if `CsvContinuousWriter` initialization fails after
  opening the file, preventing a descriptor leak on malformed appends

### API

- Drop the unused `**kwargs` passthrough from `set_cookie_value` and `retry`;
  unknown keyword arguments now raise `TypeError` instead of being silently
  ignored

## 1.0.0 — 2026-05-24

### Build and tooling

- Promote `PySocks` to a core dependency; SOCKS proxy support no longer
  requires a `[proxy]` install extra

### CLI

- Translate `SIGTERM` into `KeyboardInterrupt` for graceful shutdown so output
  writers flush before exit; a second signal restores the default handler and
  exits immediately

### Output

- Flush every record to the OS on write and `fsync` periodically (~60s) for
  crash-resilient captures
- Reject naive datetimes at the JSONL output boundary, raising `ValueError` to
  keep serialized timestamps unambiguous
- Escape CSV cells beginning with formula-trigger characters to prevent
  spreadsheet formula injection

### YouTube

- Classify member-only chats as `VideoUnplayable`
- Bound the continuation loop on repeated no-progress responses

### Twitch

- Stop VOD pagination on no-progress signals
- Make GraphQL hash-rotation failures actionable in the raised error
- Bump the live deduplication window and validate the cache limit

### Debugging

- Scrub token-shaped strings from captured debug samples

## Initial release — 2026-05-17

First release of this personal fork. Forked from
[`xenova/chat-downloader`](https://github.com/xenova/chat-downloader) at
upstream version `0.2.8`.

### Build and tooling

- Move build metadata to `pyproject.toml`; drop `setup.py`, `setup.cfg`,
  `MANIFEST.in`, `Makefile`, and `tox.ini`
- Replace `pylint` with `ruff`; add `FURB` rule family and autofixes
- Raise minimum Python requirement to 3.12
- Drop PyPI release infrastructure, pre-commit hook setup, and upstream CI

### Architecture

- Extract runtime orchestration into `chat_downloader/runtime/` package:
  `cli_bridge`, `chat_pipeline`, `runner`, `session_lifecycle`,
  `site_dispatch`
- Split `utils.py` monolith into focused modules under
  `chat_downloader/utils/`: `color_utils`, `console_utils`,
  `conversion_utils`, `dict_utils`, `json_utils`, `retry_utils`,
  `string_utils`, `time_utils`, `timed_utils`
- Split YouTube site into ~45 focused modules under `sites/youtube/`,
  grouped into bootstrap, client, continuation, discovery, parsing, and
  video subsystems
- Split Twitch site into focused modules under `sites/twitch/`: GraphQL
  client, IRC transport, live and replay services, badge handling, and
  parsing
- Extract shared site layer: `sites/base`, `session`, `retry`, `filters`,
  `models`, `remap`
- Split output writers into dedicated modules under
  `chat_downloader/output/`

### Typed API

- Add `DownloaderConfig`, `ChatRequest`, `RunConfig` typed dataclasses in
  `models.py`; drive the CLI from dataclass metadata
- Validate proxy URLs, cookie domains, and unknown `run()` kwargs at
  system boundaries
- Add default HTTP connect and read timeouts
- Add safe cookie defaults; raise `InvalidParameter` for unscoped or
  malformed cookie domains
- Expose `propagate_interrupt` on `run()` for embedding in larger
  applications

### YouTube

- Harden auth client: SAPISIDHASH-style authorization from cookie jar
- Initialize `PREF` cookie with `hl=en` and `tz=UTC`
- Honor message-type filters and recognize creator-goal actions
- Validate message groups; raise on exhausted initial 5xx retries
- Add request-profile fallback: rotate `youtube_web`, `youtube_android`,
  `youtube_ios` after repeated incomplete continuation payloads
- Centralize poll-delay policy; clamp continuation delay to 0.5–8 seconds

### Twitch

- Forward Twitch auth-token cookie to GraphQL requests
- Validate proxy URLs; raise early when PySocks is missing for SOCKS
  proxies
- Coerce Twitch sub-plan strings and preserve new IRC tag variants
- Refresh persisted GraphQL operation hashes
- Preserve Shared Chat attribution fields in parsed messages

### Output

- Promote live JSON captures to JSONL; skip duplicate output paths
- Add crash-safe `ContinuousWriter` and `ContinuousFileWriter` lifecycle
- Suppress writer-close and destructor errors on shutdown
- Add `formatting/` package with `ItemFormatter` and safer live format
  variants

### Testing

- Migrate test suite from `unittest.TestCase` to `pytest`
- Add offline unit suites for runtime, output, YouTube, and Twitch
  subsystems
- Add curated fixture library under `tests/fixtures/` for parser,
  error, and live-event regression cases
- Gate all network-dependent tests with `@pytest.mark.network`
- Enforce 100% offline coverage as a quality gate

### Documentation

- Add Markdown `README.md`; retire `README.rst`
- Add `docs/`: CLI usage guide, Python API reference, YouTube integration
  guide, Twitch integration guide, and development workflow guide
- Add `CLAUDE.md` and `AGENTS.md` for AI-assisted development workflow

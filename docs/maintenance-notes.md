# Maintenance Notes

Design decisions, deferred refactors, and non-obvious architectural choices
that are not obvious from the code or git history.

## History remaster and round naming (2026-06)

The fork-owned history on `maintainability-pass` was curated into a sequence of
focused, signed commits. Context a future contributor needs:

- **Curated history is canonical.** `maintainability-pass` carries the curated,
  signed commit series. The earlier high-volume working history (the original
  per-step microcommits) is preserved unchanged on the
  `backup/maintainability-pass-before-remaster` branch and in the signed
  `archive/pre-remaster-2026-06` tag, so nothing was lost.
- **Upstream ancestry is intact.** The fork descends from upstream
  `xenova/chat-downloader` at tag `v0.2.8` (commit `94ed3fe`); that boundary and
  every commit at or below it are inherited verbatim and were not rewritten.
- **Round naming.** Maintenance work is grouped into descriptive `Round-NN`
  milestones (`Round-02` … `Round-12`, with sub-steps such as `Round-08.1a`).
  The earlier opaque single-letter labels (`S1`, `T2`, `V1a`, `X8`, …) are
  retired from the docs; the full old→new legend lives with the remaster
  artifacts. Use descriptive round references in new notes — not letter codes.
- **Branch coverage is informational.** 100% offline *line* coverage is the
  enforced gate (`make ci`). Branch coverage was measured during the Round-12
  triage and left disabled by design; see that section.
- **Cadence.** The structural extraction campaign (Round-05 … Round-09) and the
  typed-payload migration (Round-11) are closed. Ongoing work is normal,
  opportunistic maintenance — extract or retype only when feature work crosses a
  gate, never as a standalone round.

## Deferred cross-site deduplication

Three clusters of duplicated logic were flagged during the maintainability
pass (2026-06) and intentionally left unmerged. The decision for each is
documented here so a future contributor does not re-litigate it.

### 1. Remapping tables

**Files involved**

- `src/chat_downloader/sites/twitch/remappings.py` (~324 LOC)
- `src/chat_downloader/sites/youtube/constants_message.py` (~324 LOC)
- Shared machinery: `src/chat_downloader/sites/remap.py::Remapper`

**Why they look similar**

Both sites build large `dict[str, Remapper | str]` tables that are fed to
`Remapper.remap_dict`.  The table-building functions follow the same pattern:
literal field-name strings mapped to `Remapper` instances wrapping site-specific
parsing helpers.

**Why they were not merged**

The field semantics diverge per platform: YouTube uses `camelCase` InnerTube
field names; Twitch uses IRC tag keys and GraphQL snake_case fields.  The
transformations (colour handling, currency parsing, badge structures) are
platform-specific.  A shared declarative format would need to express both
domains without becoming a new DSL, and the two sites rarely add fields in
lockstep.

**Possible future abstraction**

A small declarative table format (e.g. a typed `FieldSpec` dataclass) that
both sites compile into `Remapper` dicts at import time.  Worth revisiting if a
third site is added or if the existing tables grow significantly.

---

### 2. Badge parsing

**Files involved**

- `src/chat_downloader/sites/twitch/parsing/badges.py`
- `src/chat_downloader/sites/youtube/parsing/message_content_badges.py`

**Why they look similar**

Both iterate a list of badge entries, pop known keys, build icon lists from
image URLs, and return a normalized `list[dict]`.

**Why they were not merged**

The source payloads are completely different (Twitch IRC tag strings vs YouTube
`liveChatAuthorBadgeRenderer` objects).  The surface similarity is in the output
shape, not the parsing logic.  A shared layer would require a protocol that
bridges incompatible input structures, adding indirection with no reduction in
parsing code.

---

### 3. Continuation error-recovery

**Files involved**

- `src/chat_downloader/sites/youtube/chat_streams_runtime_iteration.py`
  (`_get_chat_messages` — profile-fallback loop)
- `src/chat_downloader/sites/twitch/live_service.py` / `irc_transport.py`
  (IRC reconnect loop)

**Why they look similar**

Both loops catch transport-level errors, decide whether to retry or surface the
exception, and apply some form of back-off or state reset before continuing.

**Why they were not merged**

The transport mechanisms are fundamentally different: YouTube uses HTTP long-poll
continuations with a profile fallback mechanism; Twitch uses a persistent IRC
socket that must re-join the channel on reconnect.  The error types, retry
strategies, and state resets are transport-specific enough that a shared retry
policy object would carry more interface than logic.

A shared `RetryPolicy` (max_attempts, back-off function, on_retry callback) is
plausible as a pure-data object if the number of retry-carrying call sites grows.

---

## Round-02 — Structural changes (2026-06)

The following splits and guardrails landed on the `maintainability-pass` branch
in June 2026 (Round-02 — module splits and durable guardrails):

### Module splits

| Before | After |
|--------|-------|
| `sites/youtube/chat_streams_runtime_iteration.py` (578 LOC) | Split into `chat_streams_context.py` (context construction) + `chat_streams_response.py` (response handling) + the loop remainder in `chat_streams_runtime_iteration.py` |
| `cli.py` (460 LOC) | Split into `cli_args.py` (parser machinery) + `cli.py` (entry point only) |
| `output/continuous_write.py` (422 LOC) | Split into `output/writers.py` (concrete writer types) + `output/continuous_write.py` (`ContinuousWriter` factory + re-exports) |

All existing import paths remain valid; test files were updated to import
directly from the new modules rather than relying on re-exports where ruff
blocked the `X as X` re-export idiom.

### Durable guardrails added

| Guardrail | How to use |
|-----------|-----------|
| Import-layering contracts | `uv run lint-imports` (wired into `make lint`); config in `pyproject.toml [tool.importlinter]` |
| Public-API snapshot | `tests/test_public_api_unit.py` — update the frozen sets when intentionally changing `__all__` |
| Module-size gate | `tests/test_module_size_unit.py` — ceiling tightened to 400 (Round-03); `chat_downloader.py` allowlisted (docstring-dominated facade); `utils/timed_utils.py` split into two sub-400-LOC modules in Round-05.1 and removed from allowlist |

### Modules still over 360 LOC (intentional)

| Module | LOC | Reason |
|--------|-----|--------|
| ~~`utils/timed_utils.py`~~ | ~~422~~ | Split into `timed_input.py` + `timed_generator.py` (Round-05.1); removed from allowlist |
| `chat_downloader.py` | 416 | Docstring-dominated thin facade; `get_chat` docstring alone is ~90 lines. No split — harms single public entry point |
| `sites/twitch/extractor.py` | 394 | Single-class extractor; split only if a seam emerges |
| `sites/twitch/replay_service.py` | 353 | Reduced from 377 LOC (Round-05.2 extracted helpers to `_replay_vod_loop.py`) |
| `sites/youtube/client_requests_continuation.py` | 367 | Cohesive HTTP-layer module |
| `cli_args.py` | 362 | Newly extracted argument machinery; just above budget |
| ~~`utils/console_utils.py`~~ | ~~316~~ | Split out `utils/filename_utils.py` (Round-06.1); console output and encoding detection remain cohesive (Windows fast-path + POSIX fallback share the `out` stream); filename sanitization is independent. `console_utils.py` drops to ~245 LOC. |

---

## Round-03 — Typing pass (2026-06)

The following typing improvements and guardrails landed on the
`maintainability-pass` branch in June 2026 (Round-03).

### Typed JSON foundation (`utils/json_types`)

`src/chat_downloader/utils/json_types.py` is a new leaf module that provides:

- PEP 695 recursive `type` aliases: `JSONScalar`, `JSONList`, `JSONDict`,
  `JSONAny`.
- Narrowing accessors `get_str`, `get_int`, `get_float`, `get_bool`,
  `get_dict`, `get_list`, `dig` — all accept `Mapping[str, object]` and return
  a concrete type, never `Any`.

Payload-parsing modules that used to write `x: dict[str, Any] = resp.get(key)`
now call `get_dict(resp, key)` and receive a fully typed `JSONDict`.  Modules
still using `dict[str, Any]` for accumulators (dicts built by assigning
heterogeneous parsed values) are intentionally left as-is; they are not JSON
boundaries and cannot be typed with `JSONDict` without cascading TypedDicts.

### New guardrails

| Guardrail | File | How to use |
|-----------|------|-----------|
| Facade param-sync drift test | `tests/test_facade_param_sync_unit.py` | `uv run pytest -q tests/test_facade_param_sync_unit.py`; fails if `get_chat()` gains a param not in `ChatRequest` |
| `Any`-density ratchet | `tests/test_any_density_unit.py` | `uv run pytest -q tests/test_any_density_unit.py`; fails if any module's `Any` count exceeds its baseline; do not raise baselines, only lower them |

### Modules with large `Any` baselines (genuine boundaries, not debt)

Files with the highest post-Round-03 `Any` counts were classified and
commented in `tests/test_any_density_unit.py`.  The main categories:

- **Formatter internals** (`formatting/format.py: 33`): format-spec dicts are
  loaded from user-supplied JSON files; `dict[str, Any]` is the stable boundary.
- **Accumulator dicts** (Twitch IRC parsers, YouTube continuation parsers):
  `info: dict[str, Any]` is populated with heterogeneous parsed values; `JSONDict`
  cannot express this without full TypedDict coverage of each message shape.
- **Generic utilities** (`utils/dict_utils.py: 19`): cross-type dispatch helpers
  that are legitimately generic.

---

## Round-04 — Cohesion splits (2026-06)

The following module extractions landed on the `maintainability-pass` branch
in June 2026 (Round-04).  Both are cohesion fixes, not size-driven
(the host files were already under the 400-line ceiling).

### Module splits

| Before | After | Round |
|--------|-------|-------|
| `sites/models.py` — contained `_ChatOutputDispatcher`, `ChatOutputWriter`, `SUPERCHAT_DEDUP_TYPES` | Moved to new `sites/output_dispatch.py` | Round-04.1 |
| `debugging.py` — contained `REDACTED`, `sanitize_for_log`, `capture_debug_sample`, and helpers | Moved to new `redaction.py` | Round-04.2 |

### Design decisions

**`_ChatHost` Protocol (Round-04.1):** `_ChatOutputDispatcher.__init__` originally took
`Chat` directly, which would have required a `TYPE_CHECKING` forward-reference
cycle after extraction.  Instead, a narrow structural Protocol `_ChatHost`
was defined in `sites/output_dispatch.py` exposing only the four members the
dispatcher actually uses (`title`, `id`, `format`, `_register_seen_message_id`).
`Chat` satisfies it structurally — no import of `Chat` needed in the new module.

**`supports_colour` stays in `debugging.py` (Round-04.2):** `supports_colour()` exists
solely to choose between `colorlog.StreamHandler` and `logging.StreamHandler`
at module load time.  It is not an independent concern — moving it would add a
cross-module import for a function with no other callers.  Colour detection
remains beside the handler it serves.

**One-way dependency:** `redaction.py` imports `debugging.logger` (the already-
configured logger instance); `debugging.py` does not import from `redaction`.
This keeps the cycle free and ensures `redaction.py` is a leaf-like module with
no upward dependency on the logging setup it uses.

### `Any`-density baseline changes (Round-04)

| Module | Before | After | Reason |
|--------|--------|-------|--------|
| `sites/models.py` | 20 | 13 | dispatcher callbacks moved out |
| `sites/output_dispatch.py` | — | 9 | new module (moved boundary) |
| `debugging.py` | 8 | 3 | redaction functions moved out |
| `redaction.py` | — | 6 | new module (moved boundary) |

---

## Round-05 — Cohesion / complexity pass (2026-06)

Round-05 work on the `maintainability-pass` branch.

### Module splits (Round-05.1, Round-05.2)

| Before | After | Round |
|--------|-------|-------|
| `utils/timed_utils.py` (422 LOC) — mixed console-input and generator-timeout concerns | `utils/timed_input.py` (~135 LOC) + `utils/timed_generator.py` (~255 LOC); import-independent (local poll constant in `timed_input`) | Round-05.1 |
| `sites/twitch/replay_service.py` — `_VodLoopPlan`, `_init_vod_loop`, `_classify_empty_page` inline | New `sites/twitch/_replay_vod_loop.py` (~70 LOC); `replay_service.py` 377→353 LOC | Round-05.2 |

### Complexity extractions (Round-05.2, Round-05.3)

| Function | Extracted helper | Result |
|----------|-----------------|--------|
| `iter_vod_chat_messages` | `_init_vod_loop` + `_classify_empty_page` (moved to `_replay_vod_loop.py`) | McCabe 14→13; `# noqa: C901` retained |
| `_get_chat_messages` | `_recover_incomplete_continuation` | McCabe 13→12; `# noqa: C901` retained |

### Design decisions

**`chat_downloader.py` keep-decision:** At 416 LOC, `chat_downloader.py` exceeds
the 400-LOC ceiling but remains allowlisted. The `get_chat()` API docstring alone
is ~90 lines; the implementation is a thin dispatch facade with no independent
logic to extract.  Splitting would fragment the single public entry point without
reducing cognitive complexity.

**Import independence (Round-05.1):** `timed_input.py` uses a local `_INPUT_POLL_SECONDS
= 0.1` constant rather than importing `POLLING_TIME` from `timed_generator.py`.
This keeps the `utils` package a leaf in the import graph (import-linter
contract) and means the two modules have zero cross-dependency.

### `Any`-density baseline changes (Round-05.1, Round-05.3)

| Module | Before | After | Reason |
|--------|--------|-------|--------|
| `utils/timed_utils.py` | 14 | removed | file deleted; split into two modules |
| `utils/timed_generator.py` | — | 12 | new module (Generator/Callable/Queue signatures) |
| `utils/timed_input.py` | — | 3 | new module (default param + cast + import) |
| `sites/youtube/chat_streams_runtime_iteration.py` | 8 | 9 | new `_recover_incomplete_continuation` adds one `dict[str,Any]` param |

---

## Round-06 — Cohesion / complexity pass (2026-06)

Round-06 work on the `maintainability-pass` branch.

### Module split (Round-06.1)

| Before | After | Round |
|--------|-------|-------|
| `utils/console_utils.py` (316 LOC) — mixed console I/O and filename sanitization | `utils/filename_utils.py` (~80 LOC); `console_utils.py` drops to ~245 LOC | Round-06.1 |

### Design decisions

**Console I/O cohesion boundary (Round-06.1):** The Windows fast-path (`_windows_write_string`
and its four helpers) and the POSIX/buffer fallback are both branches of `safe_print`
and share the `out` stream and encoding logic.  They are one cohesive concern.
Filename sanitization (`sanitize_filename_component`, `_RESERVED_WINDOWS_NAMES_RE`,
`_MAX_FILENAME_BYTES`) has zero shared state with console I/O, uses a separate
`import re`, and has zero `Any`.  The split is clean.

**`cli_args.py` kept whole (Round-06 DECLINE):** `parse_header`, `str2bool`, and
`splitter` are argparse `type=` converters that import `argparse` for
`ArgumentTypeError` and are consumed only by parser-builder code in the same file.
Extraction would carry the argparse coupling to a new module and require a back-import
— adding indirection without removing any.  File is 362 LOC (below the 400 ceiling;
the ~2-over-360 soft line is accepted, per Round-02 note above).

**`_get_continuation_info` complexity (Round-06.2, Option A failed → documented):**
Extraction of the missing-continuation guard into a `_handle_missing_live_chat_continuation`
helper was attempted.  The helper's `json_response: dict[str, Any]` parameter would
raise the module `Any`-density baseline from 8 → 9, violating the ratchet.  The function
passed the then-current McCabe-8 gate exactly; ruff's RUF100 rule also rejected
a preemptive `# noqa: C901` when the check passed.  Recorded in
`maintenance-backlog.md` (Round-06.2 entry): next edit adding a complexity branch must address
the extraction at that time, accepting the one-unit Any-baseline rise or finding a
zero-Any approach.

### `Any`-density baseline changes (Round-06.1)

| Module | Before | After | Reason |
|--------|--------|-------|--------|
| `utils/console_utils.py` | 7 | 7 | unchanged (sanitize block had zero Any; console I/O Any stays) |
| `utils/filename_utils.py` | — | 0 | new module; no Any |

---

## Round-07 — Cohesion / complexity pass (2026-06)

Round-07 work on the `maintainability-pass` branch.

### Module split (Round-07.1)

| Before | After | Round |
|--------|-------|-------|
| `sites/youtube/client_requests_continuation.py` (367 LOC) — mixed error/retry helpers and continuation orchestration | `sites/youtube/client_requests_errors.py` (~245 LOC, error/retry layer) + `client_requests_continuation.py` (~120 LOC, orchestration only) | Round-07.1 |

### Round-06.2 resolution (Round-07.1)

The Round-06.2 deferral is resolved in Round-07.1.  The
`_handle_missing_live_chat_continuation` helper (Option A from Round-06.2) now lives in
`client_requests_errors.py` rather than in `client_requests_continuation.py`.
The `json_response: dict[str, Any]` parameter that blocked the extraction in
Round-06 (it would have pushed `continuation.py` baseline from 8 → 9) now lands
in the *new* module instead, leaving `continuation.py` at 6.  `_get_continuation_info`
McCabe drops from the gate-exact 8 to ~6, restoring complexity headroom for
future edits.

### Design decisions

**Import direction (Round-07.1):** `continuation.py → client_requests_errors.py →
continuations.py`.  One-directional; no Protocol shim needed (unlike Round-04.1's
`_ChatHost`).  `continuations.py` has no back-import of either module.
`RetryPolicy` is never instantiated in the errors module — only received as a
parameter — so it goes under `TYPE_CHECKING`, keeping the errors module import-
lean.

**`sites/twitch/extractor.py` kept whole (Round-07 DECLINE):** `TwitchChatDownloader`
is a single class already maximally delegated to extracted services
(`graphql_client.py`, `replay_service.py`, `live_service.py`, `irc_transport.py`).
The GraphQL plumbing (`_client_id_kwargs`, `_download_base_gql`, `_download_gql`,
`_update_badge_info`) is `self`-bound glue around the free functions that already
live in `graphql_client.py`; relocating it would require passing `self` back in
— adding indirection without removing any.  The file is 394 LOC (6 under the
ceiling); much of the size is the static `_TESTS` table (~58 lines) and
docstrings.  Mirrors the Round-06 `cli_args.py` decline.  Pre-approved escape
hatch if a future edit crosses 400: relocate the `_TESTS` table to a zero-`Any`,
zero-logic `_test_cases.py` data module rather than fracturing the class.

**Round-07 scan: no further clean seam (STOP):** The other near-ceiling files
(`parsing/message_irc_resolve.py` 352 LOC / 14 Any, `irc_transport.py` 349 LOC,
`replay_service.py` 353 LOC, `cli_args.py` 362 LOC) have no high-value/low-risk
seam.  All are 40–50 lines under the ceiling and stable; a forced split would
add a module, import, and baseline churn to shave lines off files that are not
growing.  These files are monitored but deferred by design.

### `Any`-density baseline changes (Round-07.1)

| Module | Before | After | Reason |
|--------|--------|-------|--------|
| `sites/youtube/client_requests_continuation.py` | 8 | 6 | error/retry helpers moved out |
| `sites/youtube/client_requests_errors.py` | — | 4 | new module; moved helpers + Round-06.2 param + duplicated `Any` import |

---

## Round-08 — Lint-floor / seam-tests pass (2026-06)

Round-08 work on the `maintainability-pass` branch.

### Round-08.1 — Ruff rule expansion

Added eleven new rule families to `pyproject.toml [tool.ruff.lint] select` in
four graduated steps:

**Round-08.1a (zero-cost):** `RSE`, `PGH`, `ISC`, `FLY`, `INT`, `PLE`, `DTZ`, `PT` —
no source changes; zero violations in src at adoption.  PGH additionally
enforces code-specific `# noqa` annotations going forward (bare `# noqa` is
now a lint error).

**Round-08.1b (mechanical fixes):** `PERF`, `G`, `BLE`, `PLW`, `ARG`, `A` — ~20
violations.  Notable: PERF102 → `.values()` in `base.py`; G004 → `%`-args
logging in `twitch/replay_service.py`; A002 → rename or `# noqa` for public
`format`/`id` params.

**Round-08.1c (exception discipline):** `EM`, `S`, `TRY` — EM101/EM102 auto-fixed via
`--unsafe-fixes` in ~10 files; S101 asserts converted to `if … raise` in src;
S105 noqa for IRC anonymous-password constant; TRY301 noqa ×2 for intentional
re-raise-into-outer-handler patterns; TRY003 globally ignored (inline
exception messages are more readable; EM already governs message hygiene).

**Round-08.1d (naming subset + declines):** `N` — N818 globally ignored (exception
names are in the frozen public API snapshot; renaming breaks
`test_public_api_unit.py`); N813 (12) noqa with reason; N806 (6) noqa for
Windows API names; N802 and N811 fixed.

**Declined families (Round-08.1 evaluation):**

| Family | Violations | Decision | Reason |
|--------|-----------|----------|--------|
| `FBT` | 109 | DECLINE | Positional-bool rule fights frozen public signatures (`ChatRequest`, `Chat`, `BaseChatDownloader`) |
| `SLF` | 53 | DECLINE | Cross-module `_`-prefixed helper access is the deliberate pattern created by the Round-05–07 extractions |

### Round-08.2 — Seam unit tests

Added direct unit tests for modules extracted in Round-05–07 that were
previously covered only indirectly:

- `tests/test_youtube_client_requests_errors_unit.py`: 20 tests covering
  `_handle_http_error`, `_handle_json_api_error`, and
  `_handle_missing_live_chat_continuation`.
- `tests/test_twitch_replay_vod_loop_unit.py`: 13 tests covering `_VodLoopPlan`
  (frozen/slots dataclass), `_init_vod_loop` (offset/filter setup), and
  `_classify_empty_page` (parametrized edge dispositions).

Also added `# pragma: no cover` to the unreachable defensive guard in
`message_items_content_parser.py` (lines set then immediately checked) to
restore 100% coverage after the Round-08.1c assert→if-raise conversion.

### Round-08.3 — TypedDict investigation (DECLINE)

`sites/twitch/parsing/messages.py` (14 Any) and `message_irc_resolve.py`
(14 Any) use `info: dict[str, Any]` as a mutable IRC-tag accumulator built
from remapping tables across ~13 function signatures each.  A `total=False`
TypedDict would enumerate ~40+ optional keys and fight intermediate state
(fields consumed by one resolver before being re-set by another).  The
remapping writes make even a return-type TypedDict impractical.

Decision: move both modules to the Any-density "Out of scope" table.  Do not
reopen without a third site or a zero-boilerplate TypedDict approach.  The
Any-density baselines (14 each) are stable and must not be raised.

---

## Round-09 — Cohesion / complexity pass (2026-06)

Round-09 work on the `maintainability-pass` branch.

### Function decompositions

| Function | Extracted helpers | Result |
|----------|-------------------|--------|
| `video_status.py::parse_video_details` (~109 LOC) | `_log_player_response_shape`, `_derive_duration` → `video_status_helpers.py` | Body shrinks to ~77 LOC; redundant `microformat`/`player_microformat` locals collapsed into `player_renderer` |
| `chat_streams_context.py::_build_chat_context` (~106 LOC) | `_build_continuation_urls`, `_build_message_filters`, `_apply_session_headers` (same module) | Body shrinks to ~78 LOC; orchestrator reads as a linear named-step sequence |

### Design decisions

**Helpers home (Round-09.1):** `_log_player_response_shape` and `_derive_duration`
join `_determine_*`/`_extract_*` in `video_status_helpers.py` — the
established home for `parse_video_details`' sub-steps.  Both use
`Mapping[str, object]` params (from `collections.abc`, under `TYPE_CHECKING`)
so no new `Any` is introduced; `logger` and `float_or_none` imports are
removed from `video_status.py` as they are no longer needed there.

**Redundant local collapsed (Round-09.1):** `player_response_info["microformat"]
["playerMicroformatRenderer"]` was computed twice — once as `player_renderer`
(via `multi_get`) and again as `player_microformat` (via two `.get()` calls).
The two locals are semantically identical; `player_microformat` is removed and
`player_renderer` is passed to `_log_player_response_shape`.

**`loop_state` type tightened (Round-09.2):** `_apply_live_timing`'s `loop_state`
parameter was typed `Any`; tightened to `ContinuationLoopState` (the only
type ever passed by the single call site in
`chat_streams_runtime_iteration.py`).  This frees one `Any` slot to
accommodate the new `_apply_session_headers(ytcfg: dict[str, Any])` helper
without raising the module baseline.

**`_apply_session_headers` SLF pattern (Round-09.2):** takes `self:
YouTubeDownloaderProto` directly — the deliberate cross-module `_`-helper
pattern blessed by the Round-08.1d SLF decline.  No Protocol shim needed;
`YouTubeDownloaderProto` already exists in `_protocols.py`.

**Dead `skip_mode="none"` branch removed (Round-09.2):** the original
`TimeRangeFilter(skip_mode="first_page" if is_replay else "none")` inside
`if is_replay` was dead: the `"none"` branch could never be reached because
the `else None` at the enclosing ternary already handles the non-replay case.
`_build_message_filters` unconditionally uses `skip_mode="first_page"`.

### `Any`-density baseline changes (Round-09.1, Round-09.2)

All three files hold their existing baselines; no ratchet update required.

| Module | Baseline | After | Reason |
|--------|----------|-------|--------|
| `sites/youtube/video_status.py` | 8 | 8 | logging + duration blocks moved; import removals balanced |
| `sites/youtube/video_status_helpers.py` | 6 | 6 | new helpers use `Mapping[str, object]` — no new `Any` |
| `sites/youtube/chat_streams_context.py` | 8 | 8 | `loop_state: Any→ContinuationLoopState` offsets new helper param |

### Deferred declines reaffirmed (Round-09 STOP)

The following items remain closed; do not reopen without a third site, a new
cross-site abstraction, or a seam that does not exist today:
- Cross-site remapping/badge/retry dedup (§1–3 above).
- YouTube mixin consolidation.
- FBT / SLF ruff families (Round-08.1d).
- `chat_downloader.py` / `extractor.py` / `cli_args.py` size ceilings.
- `_get_initial_info` (113 LOC, `# noqa: C901` intrinsic).

---

## Round-11 — Typed-payload migration (2026-06)

Migrated YouTube payload-parsing modules off `dict[str, Any]` boundaries to
`JSONDict` (and related `json_types` aliases/accessors).  One commit per
module.  All residual `Any` in migrated modules falls into one of these
intentional categories — do not reopen:

- **Transport/callable objects** — `session: Any`, `response: Any`,
  `session_get: Callable[..., Any]`, `sapisidhash_generator: Any`.
- **Public-API params** — `params: ChatRequest | dict[str, Any] | None`
  (user-facing, frozen signature).
- **Assembled-output accumulators** — dicts built by assigning heterogeneous
  parsed values (`info`, `data`, `details`, `continuation_params`, `context`,
  yielded item dicts).  These are not raw API payloads and cannot be typed with
  `JSONDict` without full TypedDict coverage of each message shape.

### Round-11.2 — YouTube chat-streams pipeline (2026-06)

Migrated 7 modules in `sites/youtube/` off raw `dict[str, Any]` payload
boundaries.

**Dedup:** deleted `_safe_get_dict` from `helpers.py` (verbatim reimplementation
of `json_types.get_dict`); repointed callers.  Test covering the deleted helper
removed from `tests/test_youtube_helpers_unit.py`.

`client_requests_bootstrap.py` needed `cast("JSONDict", continuation_info)` at
one assignment site (`dict[str, str]` is narrower than `JSONDict`) and an
explicit `payload: JSONDict = {...}` annotation to prevent mypy widening the
dict literal to `dict[str, Collection[str]]`.

Skipped: `client_requests_initial.py` — its `-> tuple[Any, Any, Any]` return
cascades into 4 discovery consumers.  Resolved in Round-11.4.

| Module | Any before | Any after |
|---|---|---|
| `sites/youtube/helpers.py` | 11 | 2 |
| `sites/youtube/continuation_loop_runtime.py` | 9 | 0 (entry deleted) |
| `sites/youtube/chat_streams_context.py` | 8 | 2 |
| `sites/youtube/client_requests_errors.py` | 4 | 2 |
| `sites/youtube/chat_streams.py` | 6 | 4 |
| `sites/youtube/chat_streams_runtime_iteration.py` | 9 | 2 |
| `sites/youtube/client_requests_bootstrap.py` | 16 | 3 |

### Round-11.3 — YouTube parsing layer (2026-06)

Migrated 6 modules in `sites/youtube/parsing/`.

**Residual taxonomy:** assembled message output accumulators (`info`, `data`,
`message_info`, `message_emotes`) and handler/writer contracts
(`_append_run`'s params, `_parse_runs`/`_parse_thumbnails` returns) carry
`dict[str, Any]` by design — output of the parsing pipeline consumed by the
broader message-assembly layer which is itself `Any`-typed.  The frozen
boundary-table entries for `message_items_content_parser.py` and
`actions_router.py` (Round-08.3-declined accumulator pattern) reflect this.

| Module | Any before | Any after |
|---|---|---|
| `sites/youtube/parsing/message_content_badges.py` | 9 | 3 |
| `sites/youtube/parsing/message_items_video.py` | 7 | 2 (entry deleted) |
| `sites/youtube/parsing/actions_handlers_validation.py` | 7 | 5 |
| `sites/youtube/parsing/message_content_text_parser.py` | 14 | 7 |
| `sites/youtube/parsing/actions_router.py` | 11 | 9 |
| `sites/youtube/parsing/message_items_content_parser.py` | 13 | 10 |

### Round-11.4 — Discovery and initial-page layer (2026-06)

Closed the Round-11.2-skipped `client_requests_initial.py` and narrowed the raw-JSON
boundaries it feeds.  Aligns `_get_initial_info`'s return type with the
already-typed sibling `get_innertube_video_bootstrap` in
`client_requests_bootstrap.py` (both now return
`tuple[JSONDict, JSONDict, JSONDict]`).

**`discovery_channels_runtime_iteration.py`:** renamed the while-loop
`yt_info` binding to `cont_yt_info` to avoid a mypy type-narrowing conflict
(`_get_initial_info` infers `JSONDict`; the continuation result is
`JSONDict | None`).

**`discovery_helpers.py`:** replaced chained `.get()` calls in
`_iter_playlist_urls` with `dig()` to avoid calling `.get()` on a
`JSONAny` intermediate; replaced direct `[]`-subscript chains in
`_get_testing_items` with `multi_get` traversal (consistent with
`_get_rendered_content`'s own pattern).

**`client_context.py` / `client_auth.py`:** used `get_str(ytcfg, key)` in
place of `ytcfg.get(key)` where the result is passed to string-typed callers
(`_parse_data_sync_id`, `headers[...]`).

Item-list `list[Any]` parameters (`_extract_playlist_items`, `_process_page_items`)
are residuals: `_fetch_browse_continuation` returns `JSONList = list[JSONAny]`
and lists are invariant — changing to `list[JSONDict]` would require a cast at
every call site.  Recorded as intentional (same stop condition as the Round-11.2 skip).

| Module | Any before | Any after |
|---|---|---|
| `sites/youtube/client_requests_initial.py` | 7 | 4 |
| `sites/youtube/video_metadata.py` | 9 | 4 |
| `sites/youtube/discovery_channels_runtime_iteration.py` | 10 | 9 |
| `sites/youtube/discovery_helpers.py` | 9 | 6 |
| `sites/youtube/client_context.py` | 9 | 6 |
| `sites/youtube/client_auth.py` | 8 | 6 |

### Round-11.5 — Twitch typed-payload migration (2026-06)

Closed the Twitch side of the typed-payload track (Round-11.1–11.4 covered
YouTube only; Twitch had zero `json_types` adoption).  Five modules migrated off
raw `dict[str, Any]` JSON boundaries onto `JSONDict`/`JSONList`/`JSONAny`
aliases and `get_dict`/`get_list`/`get_str`/`get_float` accessors.

**`discovery.py`:** `_build_user_videos_query` returns `JSONList`
(`variables: JSONDict` built separately; `cast` for list-invariant return);
`_extract_user_videos` param/return narrowed to `JSONList` / `JSONDict | None`;
`get_user_videos` body uses `get_list`/`get_dict`/`get_str` with an
`isinstance` guard per edge item (same pattern as the Round-11.4 list traversals).
Generator yields stay `dict[str, Any]` — assembled remap output, not a JSON
boundary.

**`graphql_client.py`:** `_download_base_gql` / `_download_gql` return
`JSONAny` (`response.json()` can be list or dict; `cast` at the return site);
`ops` params `JSONList`; `_handle_gql_errors` errors param `JSONList` with
`isinstance` guard before `get_str`/`get_list` on the first error element.
`extractor._download_gql` wrapper widened from `list[dict[str,Any]]` to
`Any` — same token count, resolves the list-invariant mismatch at the
allowlisted call site.

**`replay_service.py`:** JSON boundary + Round-09.2-style real-type tighten.  Raw
GraphQL: `_fetch_vod_page` return `tuple[JSONDict|None, JSONDict|None]`;
`video`/`clip` locals cast to `JSONDict|None`; `get_list`/`get_str`/`get_float`
replace `.get()` calls in bodies; `isinstance` edge guard in main loop.
Real types: `_process_vod_edge` params tightened — `edge: JSONDict`,
`badge_set: BadgeSet`, `time_filter: TimeRangeFilter`, `msg_filter: MessageFilter`,
`logger_obj: Logger` — all confirmed from call sites.  `_parse_item` result
`cast("JSONDict", ...)` inside `_process_vod_edge` threads `JSONDict` through
the generator yield, enabling `Generator[JSONDict, None, None]`.

**`replay_transport.py`:** return `tuple[JSONDict|None, JSONDict|None]`;
`variables: JSONDict`.  `info`/`comments` remain `Any` (fed by
`Callable[...,Any]` `download_gql_func`).

**`live_service.py`:** `stream_info: JSONDict|None` (raw GraphQL user node;
multi_get calls downstream unaffected).

**Not migrated (intentional):**
- `irc_transport.py` — `_parse_irc_matches` return `list[dict[str,Any]]` feeds
  from `_parse_irc_item` (Round-08.3-declined IRC accumulator); changing to `JSONList`
  would require a cast cascade.  Any stays at 6.
- `parsing/messages.py`, `parsing/message_irc_resolve.py` — Round-08.3-declined IRC-tag
  accumulators (14 each).
- `remappings.py`, `types.py`, `parsing/badges.py`, `parsing/message_emotes.py`,
  `extractor.py` — data tables, canonical badge containers, assembled output,
  or allowlisted-whole.

**Residual taxonomy** (do not reopen):
- Transport callables (`Callable[..., Any]`) — `session_post`, `download_gql_func`,
  `fetch_fn`, `irc_factory`, `message_generator`.
- Badge accumulators — `badge_info`/`subscriber_badge_info` in
  `graphql_client.update_badge_info` (these ARE the `types.py` containers).
- IRC-tag accumulators — Round-08.3-declined; `messages.py` + `message_irc_resolve.py`.
- Frozen public params — `params: ChatRequest | dict[str, Any]` in `irc_transport`.
- Assembled-output dicts — generator yields from remapping / IRC parsing.

| Module | Any before | Any after |
|---|---|---|
| `sites/twitch/discovery.py` | 15 | 10 |
| `sites/twitch/graphql_client.py` | 12 | 7 |
| `sites/twitch/replay_service.py` | 18 | 3 |
| `sites/twitch/replay_transport.py` | 6 | 3 |
| `sites/twitch/live_service.py` | 5 | 4 |

### Round-12 — Branch-coverage triage (2026-06)

Measured branch coverage with `PYTHONHASHSEED=0 uv run coverage run --branch
-m pytest -q -p no:rerunfailures -m "not network"`.  Meaningful parser,
runtime, output, and discovery alternate paths were covered with focused unit
tests.  The branch report moved from 41 partial branches / 99.38% branch
coverage to 14 partial branches / 99.79% branch coverage, with 0 line misses.

`branch = true` remains disabled in `[tool.coverage.run]`.  The remaining gaps
are low-value structural or defensive/platform-specific arcs (for example
current-directory output setup, absent optional debug snapshot state, Windows
console fallback, and timed-input/thread shutdown races).  Reaching a
trustworthy 100% branch gate from here would require artificial tests or broad
branch exclusions, so line coverage remains the enforced 100% gate.

---

## Round-13 — Kick typed-payload migration: parsing layer (2026-06)

Extended the typed-payload track from Round-11 (YouTube + Twitch) to the Kick
**parsing layer**.  Kick had zero `json_types` adoption before this round.

### Changes

Six modules narrowed: `parsing/messages.py`, `parsing/moderation.py`,
`parsing/subscriptions.py`, `parsing/pins.py`, `parsing/hosts.py`,
`parsing/events.py`.

**What changed:** raw-payload input parameters narrowed from `Any` to `object`
(or, for the `dispatch_event` frame, `Mapping[str, object]`).  The
`_PARSER_DISPATCH` callable type tightened to `Callable[[object], ...]`.
`_decode_event_data` signature narrowed from `Any → Any` to
`object → object`.  No extraction logic, output types, or public behavior
changed — annotation-only.

**What did not change:** assembled-output accumulators (`info`, `metadata`,
`author`, `result`, `host_meta`, `meta`, `badge`) remain `dict[str, Any]`.
These are not raw-payload boundaries; they are heterogeneous output containers
populated by site-specific parsing logic — the same residual category used
throughout Round-11.

### Key constraint: `_opt_str` accessor is intentionally kept

`messages.py::_opt_str(value: object) -> str | None` stringifies non-str
values via `str(value)`.  This is deliberate: Kick sends numeric ids in
`sender.id` and top-level `id` fields; `_opt_str` coerces them to strings
rather than rejecting them.  The `json_types.get_str` accessor would reject
non-str values (returning `""`), which for top-level `id` fields would trigger
`ParsingError("missing an id")`.  Therefore all `_opt_str(raw.get(...))`
extraction calls are left unchanged.  Two regression tests in
`tests/test_kick_parsing_messages_unit.py` and
`tests/test_kick_parsing_all_unit.py` lock this invariant.

### Intentional residuals (Round-13, do not reopen)

- `parsing/emotes.py` (4 Any): input is already typed (`content: str`); all
  Any are output-accumulator/return types.  Nothing to narrow.
- Non-parsing Kick modules at Round-13 time (`websocket_transport.py`,
  `live_service.py`, `api_client.py`, `replay_service.py`): deferred to
  Round-14.  `extractor.py`: frozen public-API params + ClassVar data tables;
  confirmed intentional residual — no code change.

### Any-density before → after

| Module | Before | After |
|--------|--------|-------|
| `sites/kick/parsing/messages.py` | 15 | 10 |
| `sites/kick/parsing/moderation.py` | 18 | 13 |
| `sites/kick/parsing/subscriptions.py` | 13 | 9 |
| `sites/kick/parsing/pins.py` | 11 | 8 |
| `sites/kick/parsing/hosts.py` | 7 | 5 |
| `sites/kick/parsing/events.py` | 7 | 3 |

---

## Round-14 — Kick typed-payload migration: non-parsing layer (2026-06)

Completed the Kick typed-payload track by migrating the HTTP-response and
websocket-frame boundaries in the four non-parsing modules deferred from
Round-13.  Pattern mirrors Round-11.5 (Twitch `graphql_client.py`).

### Changes

**`api_client.py` (4 → 0 Any; removed from BASELINE):**
Added `cast("JSONAny", response.json())` at both `_decode_json` and
`fetch_preloaded_messages` call sites.  `_decode_json` return type narrowed to
`JSONAny`; `fetch_channel` return narrowed to `JSONDict` (isinstance guard
already present; mypy narrows automatically).  `fetch_preloaded_messages`
return narrowed to `JSONList` using `get_list(get_dict(data, "data"),
"messages")` — same accessor pattern as Round-11.  `Any` removed from
imports entirely.

**`websocket_transport.py` (7 → 4 Any):**
Added `cast("JSONDict", frame)` in `recv()` after the isinstance guard;
changed `recv()` return, `_send()` param, and `read_frames` yield type to
`JSONDict`.  Genuine residuals kept: `_default_connector → Any` (returns
opaque websocket object with no bundled stubs), `connector: Callable[...,
Any]` (injectable factory), `self._ws: Any` (same websocket object).

**`live_service.py` (8 → 5 Any):**
Narrowed three channel-metadata params downstream of `fetch_channel →
JSONDict`: `_fetch_channel_with_retry → JSONDict`, `_resolve_channel(data:
JSONDict, ...)`, `_is_live_status(data: JSONDict) → bool`.  Added
`from chat_downloader.utils.json_types import JSONDict`.  Genuine residuals:
`frame_iterator: Callable[[KickPusherTransport], Any]` ×2 (injectable),
`_iter_chat_messages → Generator[dict[str, Any], None, None]` and
`emit(message: dict[str, Any])` (assembled-output boundary).

**`replay_service.py` (8 → 5 Any):**
Narrowed VOD-metadata and page-fetch boundaries: `_fetch_video_metadata →
JSONDict` (cast + isinstance), `_resolve_vod_window(data: JSONDict, ...)`,
`_fetch_message_page → JSONDict` (cast + isinstance; `_empty: JSONDict`
literal for the fallback).  Restructured `_iter_vod_messages` loop: replaced
chained `.get("data", {}).get("messages", [])` with
`get_list(get_dict(page, "data"), "messages")` — consistent with
`fetch_preloaded_messages`.  Cursor narrowed via `isinstance` guard
(`str | None`).  Genuine residuals: `_classify_message → tuple[dict[str,
Any] | None, bool]`, generator yield type, and `all_messages: list[dict[str,
Any]]` accumulator (assembled-output boundaries).

**`emotes.py` + `extractor.py` — no code change, confirmed residuals:**
`emotes.py`: all 4 Any are output accumulators; input is already
`content: str`.  `extractor.py`: `params: ChatRequest | dict[str, Any]` ×4
is a frozen public API shape; `ClassVar` data tables; import.  Both
intentional — do not reopen.

### Any-density before → after

| Module | Before | After |
|--------|--------|-------|
| `sites/kick/api_client.py` | 4 | 0 (removed from BASELINE) |
| `sites/kick/websocket_transport.py` | 7 | 4 |
| `sites/kick/live_service.py` | 8 | 5 |
| `sites/kick/replay_service.py` | 8 | 5 |

### Residual taxonomy (final state, all Kick modules)

| Residual category | Examples |
|-------------------|---------|
| WS-object opaque type | `self._ws: Any`, `_default_connector → Any` |
| Injectable callable | `connector: Callable[..., Any]`, `frame_iterator: Callable[..., Any]` |
| Assembled output | `emit(message: dict[str, Any])`, generator yields, `all_messages`, `_classify_message` return |
| Frozen public API | `extractor.py` `params: ChatRequest \| dict[str, Any]` |
| Output accumulator | `emotes.py` return types |

---

## Round-15 — Network lifecycle robustness pass (2026-06)

Kept provider transports separate while making their lifecycle rules
consistent. HTTP exponential backoff is capped at 60 seconds plus jitter.
Twitch IRC and Kick WebSocket reconnect streaks are bounded by
`max_attempts`, back off through the shared retry policy, and reset only after
the replacement connection receives useful traffic. This prevents an endpoint
that repeatedly accepts and immediately drops sockets from creating an
unbounded tight reconnect loop.

Cleanup now flows from `Chat.close()` through message-limit and
`TimedGenerator` wrappers into provider generators. Timed workers use
cancellation-aware queue publication, so shutdown cannot strand a worker on a
full result queue. Twitch closes partially initialized IRC connections, Kick
maps WebSocket configuration/protocol failures to reconnectable transport
errors, and both transports clear stale connection objects after close.

Kick HTTP sessions are downloader-owned rather than process-global. This
prevents proxy/header state from leaking across downloader instances and makes
normal/error shutdown deterministic. Kick VOD metadata/page requests now retry
network, malformed JSON, HTTP 429, and 5xx failures; terminal 4xx failures stop
without retry. Shared HTTP sessions reject requests after close, and the facade
replaces a closed cached site session instead of reusing it.

---

## Round-16 — Hundreds-of-hours hardening pass (2026-06)

Twitch IRC and Kick WebSocket live reads now use monotonic 180-second idle
watchdogs and a one-second minimum receive poll. This removes the former 10 Hz
idle timeout loop and converts half-open connections into the existing bounded,
backed-off reconnect path. Kick reconnects also query recent HTTP history and
use the existing bounded stable-ID cache to recover messages without replaying
duplicates when the provider still retains them.

Output writers flush every record, sync every 60 seconds, and sync once more on
close. Flush/fsync errors now propagate into run failure instead of being logged
and ignored. JSONL append mode repairs a crash-truncated tail before continuing;
CSV replacement syncs the temporary file and closes the old handle on every
success/error path.

Kick VOD pagination no longer retains every parsed message in `all_messages`.
Newest-first page batches are serialized into a `SpooledTemporaryFile`, which
rolls to disk after 1 MiB, and then read backward by page for chronological
output. Only at most 500 page offsets and one decoded page remain in memory.
Retry and timeout sleeps now use monotonic time, avoiding wall-clock/DST changes.

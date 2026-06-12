# Maintenance Backlog

Single source of truth for ongoing maintainability targets, deferred decisions,
and candidate refactors. See [`maintenance-notes.md`](maintenance-notes.md) for
the full rationale behind deferred items.

## How to use

- **Any-density floor** (frozen at X4): the per-round lowering ritual is
  retired. Do not raise baselines. Tighten opportunistically alongside typing
  work; after migrating a module run
  `rg -c "\bAny\b" src/chat_downloader/<path>` and update `BASELINE` in
  `tests/test_any_density_unit.py`.
- **Gate commands** (run after every change):
  ```
  uv run pytest -q -p no:rerunfailures -m "not network"
  uv run ruff check src/chat_downloader tests
  uv run ruff format --check src/chat_downloader tests
  uv run mypy .
  uv run lint-imports
  ```
- **Done criteria**: see `AGENTS.md § Done means`.

---

## Any-density stable boundaries

These baselines are frozen. Do not lower without re-reading `maintenance-notes.md`.

| Module | Baseline | Reason |
|--------|----------|--------|
| `formatting/format.py` | 33 | stable JSON-config ↔ formatter boundary |
| `sites/twitch/remappings.py` | 11 | remapping-table data; see cross-site dedup note |
| `sites/youtube/constants_message.py` | 3 | remapping-table data |
| `sites/twitch/parsing/messages.py` | 14 | accumulator `info: dict[str,Any]` fed by ~40+ remapping-table writes; TypedDict investigated (V3) and declined — see `maintenance-notes.md § Round-8` |
| `sites/twitch/parsing/message_irc_resolve.py` | 14 | same pattern; V3 declined — do not reopen without a third site or a zero-boilerplate TypedDict solution |

---

## Deferred-by-design

All items below have explicit rationale in
[`maintenance-notes.md`](maintenance-notes.md); do not reopen without reading it.

### Cross-site deduplication
- **Remapping tables** (`sites/twitch/remappings.py` 326 LOC vs
  `sites/youtube/constants_message.py` 328 LOC): field semantics and
  transformation logic diverge per platform; unification adds indirection without
  code reduction. Revisit only if a third site is added.
- **Badge parsing** (`twitch/parsing/badges.py` vs
  `youtube/parsing/message_content_badges.py`): source payloads are
  incompatible; shared layer would add indirection without reducing logic.

### Intrinsic complexity (noqa: C901)
The four functions below exceed the McCabe-8 gate for legitimate structural
reasons; their `# noqa: C901` annotations carry inline justifications:
- `sites/twitch/live_service.py:iter_stream_chat_messages` — live IRC reconnect
  loop
- `sites/twitch/replay_service.py:iter_vod_chat_messages` — cursor-advance
  guard, first-iteration check, and edge disposition fan-out are intrinsic (S2
  extracted `_VodLoopPlan`/`_init_vod_loop`/`_classify_empty_page` to
  `sites/twitch/_replay_vod_loop.py`; complexity reduced 14→13 but remains above
  gate)
- `sites/youtube/client_requests_initial.py:_get_initial_info` — HTTP
  status-code dispatch + retry loop
- `sites/youtube/chat_streams_runtime_iteration.py:_get_chat_messages` —
  live/replay branching, no-progress guard, and end-message injection are
  intrinsic (S3 extracted `_recover_incomplete_continuation`; complexity reduced
  13→12 but remains above gate)

### Allowlisted large modules
- ~~`utils/timed_utils.py`~~ — split into `utils/timed_input.py` (~135 LOC)
  and `utils/timed_generator.py` (~255 LOC) (S1). Both under 400 LOC;
  removed from allowlist.
- `chat_downloader.py` (416 LOC) — thin API facade; size is docstring-dominated
  (`get_chat` docstring alone is ~90 lines). No split: splitting harms the
  single public entry point and there is no independent logic to move.

---

## Candidate cohesion splits

Lower priority — coupling cost documented below.

### Round-5 cohesion / complexity pass (S-series)

#### ~~`utils/timed_utils.py`~~ → done (S1)
Split into `utils/timed_input.py` (console input: `TimeoutOccurred`, `echo`,
`win/posix_timed_input`, `timed_input`) and `utils/timed_generator.py`
(`POLLING_TIME`, `TimedGenerator`, `polling_sleep`). The two concerns share no
state. `timed_utils.py` removed from the module-size ALLOWLIST; both new modules
are well under 400 LOC. `Any`-density baselines updated (12/3 respectively).

#### ~~`iter_vod_chat_messages` in `sites/twitch/replay_service.py`~~ → done (S2)
Extracted `_VodLoopPlan`, `_init_vod_loop`, and `_classify_empty_page` to a new
`sites/twitch/_replay_vod_loop.py` (~70 LOC). `replay_service.py` drops from
377 → 353 LOC; `# noqa: C901` retained (complexity reduced 14→13, still
intrinsic).

#### ~~`_get_chat_messages` in `sites/youtube/chat_streams_runtime_iteration.py`~~ → done (S3)
Extracted `_recover_incomplete_continuation` helper; reduces the except-block
from ~8 lines to a single call. `# noqa: C901` retained (12→12, intrinsic).
`Any`-density baseline raised by 1 for the new helper's `ytcfg: dict[str, Any]`
parameter.

---

### ~~`_ChatOutputDispatcher` in `sites/models.py`~~ → done (M1)
Extracted to `sites/output_dispatch.py`. The `Chat` forward-ref cycle was
eliminated with a `_ChatHost` Protocol. `SUPERCHAT_DEDUP_TYPES` and
`ChatOutputWriter` moved alongside it. Patch targets in tests updated;
`Any`-density baseline lowered from 20 → 13 for `sites/models.py`, new entry
at 9 for `sites/output_dispatch.py`.

### ~~`debugging.py`~~ → done (M2)
Redaction + debug-sample capture (`REDACTED`, `sanitize_for_log`,
`capture_debug_sample`) extracted to `redaction.py`. Importers in
`sites/youtube/` and `runtime/` repointed; tests moved to
`test_redaction_unit.py`. `supports_colour` intentionally stayed in
`debugging.py` — it is a sub-step of the logging handler setup, not an
independent concern. `Any`-density baseline for `debugging.py` lowered
from 8 → 3; new entry `redaction.py` at 6.

---

### Round-6 cohesion / complexity pass (T-series)

#### ~~`sanitize_filename_component` in `utils/console_utils.py`~~ → done (T1)
Extracted `sanitize_filename_component`, `_RESERVED_WINDOWS_NAMES_RE`, and
`_MAX_FILENAME_BYTES` to new `utils/filename_utils.py` (~80 LOC). The filename
sanitization concern shares no state with console I/O (separate `re` usage, zero
`Any`). One non-test caller (`sites/output_dispatch.py`) repointed; tests moved to
`test_filename_utils_unit.py`. `console_utils.py` drops from 316 → ~245 LOC.
`Any`-density: `console_utils.py` baseline unchanged at 7; new entry
`utils/filename_utils.py: 0`.

#### ~~`_get_continuation_info` in `sites/youtube/client_requests_continuation.py`~~ → done (U1/T2)
The missing-continuation guard was extracted into
`_handle_missing_live_chat_continuation` in the new
`sites/youtube/client_requests_errors.py`. The `dict[str, Any]` parameter that
blocked T2 in Round-6 lands in the errors module (not the constrained
`continuation.py`). `client_requests_continuation.py` baseline lowered 8 → 6;
new entry `client_requests_errors.py: 4`. `_get_continuation_info` McCabe drops
from 8 (gate-exact) to ~6, restoring complexity headroom.

---

### Round-7 cohesion / complexity pass (U-series)

#### ~~`client_requests_continuation.py` error/retry cluster~~ → done (U1)
Extracted error/retry helpers (`_RETRYABLE_HTTP_STATUS_CODES`, `_CHALLENGE_HINTS`,
`_contains_challenge_text`, `_captcha_guidance_message`, `_apply_retry_or_raise`,
`_retry_or_raise_incomplete`, `_is_retryable_status`, `_retry_or_raise_exhausted`,
`_handle_http_error`, `_handle_json_api_error`) plus the T2 helper
`_handle_missing_live_chat_continuation` to new
`sites/youtube/client_requests_errors.py` (~245 LOC).
`client_requests_continuation.py` drops from 367 → ~120 LOC (orchestration only).
Two test files repointed (import-string change only). `Any`-density:
`continuation.py` 8 → 6; new `client_requests_errors.py: 4`.

#### `sites/twitch/extractor.py` (394 LOC) — deferred (U2 DECLINE)
Single-class site extractor already maximally delegated. GraphQL glue is
`self`-bound; forced split requires passing `self` in as a free-function
argument. 6 lines under the ceiling; stable. See `maintenance-notes.md §
Round-7` for full rationale and the `_TESTS`-table escape hatch if it crosses
400 in a future edit.

#### Round-7 scan: no further clean seam (U3 STOP)
`message_irc_resolve.py` (352), `irc_transport.py` (349), `replay_service.py`
(353), `cli_args.py` (362) — all 40–50 lines under the ceiling, stable, and
each already declined or a dense-`Any` parser where split cost ≈ benefit. No
further extraction warranted; these files are monitored but deferred by design.

---

### Round-8 lint-floor / seam-tests pass (V-series)

#### ~~Expand ruff rule set~~ → done (V1a–V1d)
Added N, EM, S, TRY, PERF, G, BLE, PLW, ARG, A, RSE, PGH, ISC, FLY, INT,
PLE, DTZ, PT families to `pyproject.toml [tool.ruff.lint] select`.  Fixed ~80
violations across src and tests; added per-file-ignores for test-incompatible
rules (S101, ARG, BLE001, EM, N802, N806).  Two families evaluated and
declined with documented rationale:
- **~~FBT~~** (109→0 violations): resolved in X5 — keyword-only migration across
  all src boolean params; FBT added to `select`. Tests suppressed via per-file-ignores.
- **SLF** (376 violations as of X5): cross-module `_`-prefixed helper access is the
  deliberate pattern created by rounds 5–7 extractions; keep declined.

#### ~~Seam unit tests for extracted modules~~ → done (V2)
Added `test_youtube_client_requests_errors_unit.py` (HTTP/JSON error handlers)
and `test_twitch_replay_vod_loop_unit.py` (`_VodLoopPlan`, `_init_vod_loop`,
`_classify_empty_page`).  Both modules had only indirect coverage; direct tests
pin their observable surface.

#### Twitch IRC-parser TypedDict — declined (V3)
`sites/twitch/parsing/messages.py` (14 Any) and `message_irc_resolve.py`
(14 Any) use `info: dict[str, Any]` as a mutable IRC-tag accumulator across
~13 function signatures each.  A `total=False` TypedDict would need ~40+
optional keys; the intermediate states differ from the final state; remapping
writes fight the TypedDict pattern.  Moved to the "Out of scope" Any-density
table above.  Do not reopen without a third site or a zero-boilerplate
TypedDict approach.

---

### Round-9 cohesion / complexity pass (W-series)

#### ~~`parse_video_details` in `sites/youtube/video_status.py`~~ → done (W1)
Extracted `_log_player_response_shape` (debug-shape logging) and
`_derive_duration` (duration fallback chain) to `video_status_helpers.py`.
Both helpers use `Mapping[str, object]` params — zero new `Any`.  Redundant
`microformat`/`player_microformat` locals collapsed into the already-computed
`player_renderer`.  `video_status.py` loses the `logger` and `float_or_none`
imports.  Function body: ~109 → ~77 LOC.  `Any`-density baselines unchanged.

#### ~~`_build_chat_context` in `sites/youtube/chat_streams_context.py`~~ → done (W2)
Extracted `_build_continuation_urls`, `_build_message_filters`,
`_apply_session_headers` to the same module.  `_apply_live_timing`'s
`loop_state: Any` tightened to `ContinuationLoopState` to offset the one new
`dict[str, Any]` param in `_apply_session_headers`; module baseline stays at 8.
Dead `skip_mode="none"` branch removed (unreachable inside `if is_replay`).
Function body: ~106 → ~78 LOC.  `Any`-density baselines unchanged.

#### ~~Seam unit tests for W1/W2 helpers~~ → done (W3)
New `tests/test_youtube_video_status_unit.py`: 5 parametrized `_derive_duration`
cases + 4 `_log_player_response_shape` branch-coverage tests.
Appended to `tests/test_youtube_chat_context_unit.py`: 2
`_build_continuation_urls` + 4 `_build_message_filters` + 1
`_apply_session_headers` tests.  Coverage stays at 100%.

#### Round-9 scan: no further clean seam (W4 STOP)
`_get_initial_info` in `client_requests_initial.py` (113 LOC, `# noqa: C901`)
is the next candidate; HTTP-status dispatch could be extracted à la U1.
Deferred: the function's complexity is documented intrinsic (status-code
dispatch + retry loop) and the file is well under the 400-LOC ceiling.
Revisit only if a future edit raises its McCabe score above 10 again.
All other candidates in the 340–400 LOC range were declined in U3 (Round-7)
and remain closed.

---

## Extraction round cadence — RETIRED (X-series)

The S→W extraction-round program is closed. Nine rounds of structural work
are complete; the mechanical decomposition seam is exhausted.

**New rule:** extract a module only when it organically crosses the 400-LOC
cap or McCabe-10 gate during feature work — never as a standalone round.

**Ongoing high-value track:** typed-payload migration (accessor pattern over
incoming YouTube/Twitch JSON via `utils/json_types` — `get_str`, `get_int`,
`get_dict`, `get_list`, `dig`). This reduces future bug surface when platforms
rotate their schemas. No scheduled cadence; do it opportunistically alongside
parser changes. After each migration, lower the affected module's baseline in
`tests/test_any_density_unit.py` (opportunistic tightening — not a scheduled
round).

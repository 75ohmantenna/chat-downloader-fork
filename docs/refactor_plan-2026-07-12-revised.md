# Refactoring Plan — 2026-07-12 (revised)

This supersedes `refactor_plan-2026-07-12.md` after an audit against the current
tree. It captures the prioritized technical-debt refactoring plan for
`chat-downloader`, focused on structural debt rather than cosmetic issues. The
codebase already enforces strong hygiene (no bare `except: pass`, no
`TODO/FIXME`, 100% `from __future__ import annotations`, strict lint/import
contracts, and 100% offline line coverage).

> **Revision note.** Three claims in the original draft were corrected against
> the tree:
> 1. `BaseChatDownloader` **is** part of the public API (exported in
>    `__all__`), so item 1 is not low-risk and cannot become a bare Protocol.
> 2. A literal `typing.Protocol` conversion of `BaseChatDownloader` is
>    infeasible — it is used with `issubclass()` and provides shared
>    `__init__` state. The reusable seam it proposes already exists as
>    `sites/_protocols.py::SessionOwnerProto`.
> 3. The YouTube consolidation targets in Phase 2 collide with the repo's own
>    400-line module-size ratchet; the plan now reconciles that explicitly.

## Highest-priority debt clusters

### 1. `sites/base.py` is a mostly pass-through wrapper

- **Location:** `src/chat_downloader/sites/base.py` (331 lines), especially lines 124–331.
- **Smell:** Most instance methods delegate one-for-one to `sites/session.py`
  with no added behavior.
- **Evidence:**
  - `clear_cookies` → `clear_session_cookies(self)`
  - `_session_post` → `session_post(self, ...)`
  - `get_session_headers` → `session_header(self, ...)`
- **Why it matters:** The delegating methods fail the deletion test — inlining
  them would not spread work.
- **Constraints discovered in audit (do not ignore):**
  - `BaseChatDownloader` is exported in `chat_downloader/__init__.py::__all__`
    and `sites/__init__.py::__all__`. It is **public API**, guarded by
    `tests/test_public_api_unit.py`. Removing or renaming it is a breaking
    change, not an internal refactor.
  - `runtime/session_lifecycle.py` performs `issubclass(cls,
    BaseChatDownloader)` and `cls == BaseChatDownloader` runtime checks. A
    `typing.Protocol` with data members cannot back `issubclass`.
  - Site classes call `super().__init__(**kwargs)` → `init_session_state`, and
    the class carries real state (`_SITE_DEFAULT_PARAMS`, `_TESTS`). A Protocol
    cannot supply that.
  - The structural seam the original draft proposed to *create* already exists:
    `sites/_protocols.py::SessionOwnerProto` captures the duck-typed session
    surface that `sites/session.py` helpers depend on.
- **Actions (revised):**
  1. Keep `BaseChatDownloader` as a **thin concrete base**: shared state init
     (`__init__`/`init_session_state`), the class vars, and the `issubclass`
     anchor stay. Do **not** convert it to a Protocol.
  2. Move the genuinely stateless utilities (`matches`, `retry`,
     `check_for_invalid_types`, `get_mapped_keys`) into a new
     `sites/common.py` with no runtime state; have the base re-export or call
     them so the public surface is unchanged.
  3. Where new code needs only the session surface, depend on the existing
     `SessionOwnerProto` instead of importing `BaseChatDownloader`.
- **Risk:** Medium (revised up from Low). Public-API surface plus the
  `issubclass` gate must stay intact; `test_public_api_unit.py` will flag drift.

### 2. YouTube package is over-split by runtime phase (temporal decomposition)

- **Location:** `src/chat_downloader/sites/youtube/` (37 files + `parsing/`).
- **Smell:** Behavior is sliced by *when* it runs in the pipeline rather than by
  domain concept.
- **Problem clusters (measured LOC):**

| Cluster | Current files (LOC) | Draft target | Ratchet check |
|---|---|---|---|
| Chat streams | `chat_streams.py` (92), `_context` (234), `_response` (140), `_runtime_iteration` (304) — Σ770 | 1–2 modules | ⚠️ 1 module = 770 > 400; even 2 modules run near the cap |
| Continuations | `continuation_loop.py` (37), `_runtime` (115), `_state` (16), `continuations.py` (239) — Σ407 | 2 modules | ✅ fits |
| Client requests | `client_requests_*` (129+133+284+182) + `client_context` (109) — Σ837 | 2 modules | ⚠️ 2 modules ≈ 418 avg > 400 |
| Video metadata | `video_initialization`/`video_metadata`/`video_status`/`_helpers`/`_models` — Σ~16K bytes | 2 modules | ✅ likely fits |
| Constants | `constants_*` (5) | 1 module + typed key groups | ✅ but `constants_message.py` is already allowlisted |
| Parsing actions | `actions_*` (4) | 2 modules | ✅ fits |
| Parsing messages | `message_*` (6, Σ598) | 3 modules | ✅ ~200 each |

- **Why it matters:** A single continuation-loop bug currently requires reading
  four files. Low locality makes reasoning and testing harder.
- **Guardrail tension (new):** `tests/test_module_size_unit.py` enforces
  `MAX_LINES = 400` for non-allowlisted modules. The chat-streams (770) and
  client-requests (837) clusters **cannot** hit the draft's target module counts
  while staying under 400. Part of the temporal over-splitting is an artifact of
  this ratchet. Before consolidating those two clusters, pick one explicitly:
  - **(preferred)** consolidate to *cohesive* modules and accept more than the
    draft's target count (e.g. chat-streams → 3, not 1–2), keeping each < 400; or
  - allowlist the merged module in `test_module_size_unit.py` with a one-line
    rationale, only if the result is genuinely one cohesive unit.
- **Actions:**
  1. Consolidate each cluster around a domain concept, not a phase, honoring the
     ratchet decision above.
  2. Use package-private names (`_continuation_loop`) so internal files cannot be
     imported directly from outside the package.
  3. Preserve existing request-profile fallback, continuation recovery, and
     visitor-data behavior; add characterization tests first if coverage is
     integration-only.
- **Risk:** Medium. YouTube has the densest test/fixture surface. Move code
  incrementally and keep the focused command green:
  `uv run pytest -q tests/test_youtube_* tests/test_offline_error_fixtures_unit.py`.

### 3. `YouTubeChatDownloader` is a god object by mixin composition

- **Location:** `src/chat_downloader/sites/youtube/extractor.py`, lines 32–41.
- **Smell:** 7 mixins plus `BaseChatDownloader` in one class definition,
  covering discovery, playlists, video metadata, initialization, streams, chat
  users routing, and chat users retrieval.
- **Why it matters:** The class definition is the interface; every
  consumer/test must reason about all seven concerns.
- **Actions:**
  1. Define narrow collaborators behind `typing.Protocol`s: `DiscoveryService`,
     `VideoMetadataService`, `ChatStreamService`, `ChatUsersService`,
     `AuthService`.
  2. Make `YouTubeChatDownloader` a thin coordinator that owns URL matching and
     delegates to injected services.
  3. Each service accepts an HTTP/session seam (`SessionOwnerProto`) for
     isolated testing.
  4. Remove/archive the seven mixins once their behavior has moved.
- **Risk:** Medium-high. This is invasive and is the one change that can
  silently alter YouTube runtime behavior. Perform it **last**, after Phase 2
  consolidation and behind the Phase 0 characterization tests, so collaborators
  map cleanly onto the consolidated modules.

## Medium-priority debt

### 4. `ChatDownloader` facade growth

- **Location:** `src/chat_downloader/chat_downloader.py` (419 lines — already
  over the 400 budget and therefore allowlisted), lines 218–381.
- **Smell:** Large portions of the facade are thin parameter plumbing:
  - `get_chat()` → `get_chat_request()` → `try_create_chat_from_sites()`
  - `create_session()` → `create_runtime_session()`
  - `close()` → `close_sessions()`
- **Actions:**
  1. Move URL validation, proxy-cookie safety checks, and request dispatch into
     `runtime/` helpers.
  2. Keep the `ChatDownloader` class as the user-facing seam, reduced to config
     + lifecycle. Target getting it back under 400 so it can leave the
     allowlist.
  3. Watch the module-size ratchet in `tests/test_module_size_unit.py`.
- **Risk:** Low. The public API must remain stable, so
  `test_facade_param_sync_unit.py` and `test_public_api_unit.py` will warn about
  drift.

### 5. Module-level mutable state (all three confirmed present)

| File | Line | Issue | Fix |
|---|---|---|---|
| `src/chat_downloader/debugging.py` | 40 | `global TESTING_MODE` mutated by test setup | Replace with `contextvars.ContextVar` or pass a flags object |
| `src/chat_downloader/sites/kick/pusher_discovery.py` | 153 | `global _PUSHER_DISCOVERED_KEY` lazy cache | Move to `KickChatDownloader` instance or an injected cache object |
| `src/chat_downloader/sites/youtube/parsing/message_items_content_parser.py` | 59 | `global _REMAPPING, _COLOUR_KEYS` lazy init used to avoid circular imports | Re-order imports or introduce a small factory; fix the circular import, don't hide it |

- **Also note:** `chat_downloader.py` lines 50–51 use module-level
  `SiteDefault` singletons to avoid a lint warning. Prefer `None` plus internal
  default replacement, or a dedicated sentinel class.

### 6. `sites/models.py` couples data to output dispatch — DONE (partial)

- **Location:** `src/chat_downloader/sites/models.py` (confirmed: imports
  `sites.output_dispatch`, `debugging`, and `utils.console_utils`).
- **Done:** `Chat` no longer imports `_ChatOutputDispatcher` at module load; the
  dispatcher is created lazily on the first `attach_writer()` via a deferred
  import, and emit/close/write_error_count guard the no-output case. The
  static model→output layering dependency is removed.
- **Deferred:** Making `Chat` a genuine pure dataclass and relocating the
  iteration/emit path into the runtime was NOT done — see risk note.
- **Risk (revised): Medium, not Low.** `Chat.__next__` emits to writers as a
  side-effect of iteration, and ~6 test files bind `chat.attach_writer` /
  `chat._output_dispatcher` as the contract. A full pure-dataclass extraction
  would change Chat's iteration contract and rewrite those tests; the injected/
  lazy-dispatcher approach was chosen to remove the coupling without that churn.
- **Still open:** the `utils.console_utils.safe_print` dependency (via
  `print_formatted`) remains; move only if `print_formatted` itself relocates.

### 7. Reverse `kick/constants.py` → `pusher_discovery.py` dependency

- **Location:** `src/chat_downloader/sites/kick/constants.py` line 18
  (`from ...pusher_discovery import resolve_pusher_key`).
- **Smell:** Constants depend on runtime discovery logic.
- **Action:** Make `pusher_discovery.py` read from `constants.py`, or move the
  shared value to a neutral `_shared` module. Confirm no new import cycle is
  introduced.
- **Risk:** Low.

## Low-priority / opportunistic cleanups

| Smell | Location | Fix |
|---|---|---|
| `# noqa: C901` on large functions | 7 files (e.g. `twitch/replay_service.py:175`, `youtube/chat_streams_runtime_iteration.py:218`) | Acceptable when documented; prefer extracting helpers |
| `# type: ignore` / `# noqa` count | 62 annotations (e.g. `utils/console_utils.py`) | Many are legitimate Windows APIs; prune stale ones opportunistically |
| `id` parameter shadows built-in | `sites/models.py:67` | Rename to `message_id` / `item_id` |
| `lambda` in `_formatter` | `sites/models.py:171` | Replace with a named function |
| Broad `except Exception` in `Chat.close` | `sites/models.py` | Catch only expected close errors and suppress them explicitly |

## What to leave alone

- **Data-heavy constants tables** (`youtube/constants_message.py`,
  `twitch/remappings.py`, `twitch/constants.py`) — intentionally large and
  allowlisted by `test_module_size_unit.py`.
- **Exception hierarchy** in `errors.py` — the 24 small classes mirror the
  platform error space well.
- **Intentional-surface snapshot tests** (`test_public_api_unit.py`,
  `test_cli_surface_unit.py`, `test_facade_param_sync_unit.py`,
  `test_makefile_contract_unit.py`) — guardrails, not debt.
- **Cross-site independence.** Do not add shared YouTube/Twitch/Kick
  abstractions for badges, retry, or parsing logic unless a genuine
  shared-maintenance case emerges; `AGENTS.md` forbids speculative cross-site
  abstraction.

## Proposed roadmap (re-ordered by risk/value)

The phases are renumbered from the original so the low-risk, high-certainty work
lands first and the one behavior-risking change (mixin god object) lands last.

### Phase 0 — Safety net
- Add fixture-backed characterization tests for YouTube continuation recovery
  and request-profile fallback if any are network-only.
- Add a package-level YouTube smoke test that hits
  `extractor → chat_streams → continuation_loop` without network.

### Phase 1 — Low-risk, self-contained wins
- Item 7 — DONE: reversed the `kick/constants.py` ↔ `pusher_discovery.py`
  dependency (`get_pusher_ws_url` moved into `pusher_discovery.py`).
- Item 5 — DONE: `TESTING_MODE` → `ContextVar`; YouTube remapping global →
  `functools.cache`; Pusher-key global → injectable `PusherKeyCache`.
- Item 6 — DONE (partial): lazy/injected output dispatcher; see item 6 above.
- Cosmetic cleanups: `_formatter` lambda → named `_default_formatter` (DONE).
  NOT done and dropped from scope: renaming the `id` param (documented public
  API, `# noqa: A002`) and narrowing `Chat.close`'s `except` (already narrow;
  the broad catch lives in `__next__` and is intentional/documented).

### Phase 2 — Collapse shallow layers
- Item 1 (revised): keep `BaseChatDownloader` a thin **concrete** base; extract
  stateless utilities to `sites/common.py`; reuse `SessionOwnerProto` for new
  seams. Do not Protocol-ify the base.
- Item 4: slim `chat_downloader.py` by moving dispatch plumbing into `runtime/`.

### Phase 3 — Consolidate YouTube by domain
- Merge chat-stream, continuation, client-request, video-metadata, and parsing
  clusters, honoring the Phase-2/ratchet decision recorded in item 2 (module
  counts may exceed the draft targets to stay under 400 LOC).
- Keep `tests/test_youtube_*` green.

### Phase 4 — Replace YouTube mixin god object (highest risk, last)
- Introduce service collaborators behind Protocols.
- Make `YouTubeChatDownloader` a coordinator.
- Archive the 7 mixins.
- Gate strictly behind the Phase 0 characterization tests.

### Phase 5 — Ratchet
- Consider a package-level file-count or import-depth gate for
  `sites/youtube/`.
- Lower `Any` density caps in `tests/test_any_density_unit.py` for cleaned
  modules.

## Verification checklist

Completing any phase is not done until:

- `uv run ruff check src/chat_downloader tests` is clean.
- `uv run ruff format --check src/chat_downloader tests` is clean.
- `uv run mypy .` is clean.
- `uv run lint-imports` is clean.
- `uv run pytest -q -p no:rerunfailures -m "not network"` is green.
- User-facing behavior, tooling, project structure, or public API changes are
  documented in the same commit.

---

*Revised 2026-07-12 after codebase audit. Supersedes
`refactor_plan-2026-07-12.md`. Deferrals and evolving notes belong in
`docs/maintenance-backlog.md` and `docs/maintenance-notes.md`.*

# Refactoring Plan — 2026-07-12 (revised)

This supersedes `refactor_plan-2026-07-12.md` after an audit against the current
tree. It captures the prioritized technical-debt refactoring plan for
`chat-downloader`, focused on structural debt rather than cosmetic issues. The
codebase already enforces strong hygiene (no bare `except: pass`, no
`TODO/FIXME`, 100% `from __future__ import annotations`, strict lint/import
contracts, and 100% offline line coverage).

> **Status (2026-07-18): complete and retired.** The evidence-backed work in
> this plan has landed. The continuation loop was reunified by cohesion and its
> composition safety net was added. Blanket consolidation of the remaining
> YouTube modules and the proposed service/mixin rewrite were declined because
> they do not remove demonstrated coupling. Ongoing targets live only in
> `maintenance-backlog.md`.

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

### 1. `sites/base.py` is a mostly pass-through wrapper — DONE (stateless-util extraction)

> **Done:** `check_for_invalid_types` and `get_mapped_keys` moved to a new
> stateless `sites/common.py`; callers (YouTube continuation, Twitch
> `validation_keys`, tests) now use the free functions, and the stale
> `check_for_invalid_types` member was dropped from `YouTubeDownloaderProto`.
> At extraction time, `base.py` shrank 331 → 294 LOC. **Not moved
> (deliberate):** `matches` stays on
> the base (class-polymorphic over `cls._VALID_URLS`; a free function would
> degrade the `site.matches(url)` dispatch API) and `retry` stays a thin alias
> over the canonical `sites/retry.py`. The base was **not** converted to a
> Protocol (see constraints below) and the session-delegation methods remain.


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
- **Outcome:** `BaseChatDownloader` remains the concrete public/session base.
  `check_for_invalid_types` and `get_mapped_keys` moved to `sites/common.py`;
  class-polymorphic `matches` and the public `retry` alias remain on the base.
  New session-only helpers use `SessionOwnerProto`.

### 2. YouTube temporal decomposition — DONE for the continuation loop

> **Done:** The six phase-oriented chat/continuation loop modules were
> reunified in cohesive `continuation.py`, with downloader-independent helpers
> in `continuation_helpers.py`. Characterization and composition tests cover
> continuation recovery, request-profile fallback, and the assembled path.
> **Declined:** No blanket file-count target remains for client requests, video
> metadata, constants, or parsing. Their current modules represent distinct
> responsibilities; revisit a boundary only when a feature or defect provides
> concrete locality evidence.

- **Historical evidence:** The chat setup, response handling, loop state, and
  iteration behavior had been spread across six modules by runtime phase. A
  continuation defect therefore required reconstructing one behavior across
  multiple files.
- **Outcome:** Those phases now live together in `continuation.py`. The module
  is intentionally allowlisted above 400 lines because splitting the stateful
  loop would restore the original coupling. Pure helpers remain separate in
  `continuation_helpers.py`.

### 3. `YouTubeChatDownloader` service rewrite — DECLINED

> The existing mixins compose stateful site capabilities. Wrapping them in
> injected services would add forwarding layers and Protocol surface without
> removing their shared downloader/session coupling. This also conflicts with
> the recorded Round-09 decision to keep YouTube mixin consolidation closed
> absent a genuinely new seam.

- **Outcome:** Keep the current composition. The mixins group public site
  capabilities while sharing one downloader/session owner; no service boundary
  has emerged that would remove coupling.

## Medium-priority debt

### 4. `ChatDownloader` facade growth — DONE

- **Done:** Extracted the proxy-cookie safety guard (`check_proxy_cookie_safety`
  + `_is_loopback_host`) into `runtime/config_guards.py`, bringing the facade
  from 416 to 388 LOC and removing it from the module-size allowlist so the
  <400 ceiling is now enforced. URL validation and request dispatch were already
  delegated to `runtime/`. The class is now config + lifecycle plumbing.
- **Location:** `src/chat_downloader/chat_downloader.py` (was 419 lines — over
  the 400 budget and allowlisted), lines 218–381.
- **Smell:** Large portions of the facade are thin parameter plumbing:
  - `get_chat()` → `get_chat_request()` → `try_create_chat_from_sites()`
  - `create_session()` → `create_runtime_session()`
  - `close()` → `close_sessions()`
- **Outcome:** The public API remains stable, and the facade is now guarded by
  the normal module-size ceiling.

### 5. Module-level mutable state — DONE

| File | Former issue | Resolution |
|---|---|---|
| `src/chat_downloader/debugging.py` | Mutable `TESTING_MODE` global | `ContextVar` |
| `src/chat_downloader/sites/kick/pusher_discovery.py` | Lazy Pusher-key global | Injectable `PusherKeyCache` |
| `src/chat_downloader/sites/youtube/parsing/message_items_content_parser.py` | Lazy remapping globals | `functools.cache` factories |

### 6. `sites/models.py` couples data to output dispatch — DONE

- **Historical issue:** `sites/models.py` imported output dispatch at module
  load, reversing the intended data/output layer direction.
- **Done:** `Chat` no longer imports `_ChatOutputDispatcher` at module load; the
  dispatcher is created lazily on the first `attach_writer()` via a deferred
  import, and emit/close/write_error_count guard the no-output case. The
  static model→output layering dependency is removed.
- **Declined:** Making `Chat` a pure dataclass and relocating its iteration/emit
  path would change its established iterator-and-writer lifecycle contract
  without removing a remaining static layer dependency.
- **Risk (revised): Medium, not Low.** `Chat.__next__` emits to writers as a
  side-effect of iteration, and ~6 test files bind `chat.attach_writer` /
  `chat._output_dispatcher` as the contract. A full pure-dataclass extraction
  would change Chat's iteration contract and rewrite those tests; the injected/
  lazy-dispatcher approach was chosen to remove the coupling without that churn.
- The `utils.console_utils.safe_print` dependency remains intentionally with
  `print_formatted`; there is no independent relocation target.

### 7. Reverse `kick/constants.py` → `pusher_discovery.py` dependency — DONE

- **Location:** `src/chat_downloader/sites/kick/constants.py` line 18
  (`from ...pusher_discovery import resolve_pusher_key`).
- **Smell:** Constants depend on runtime discovery logic.
- **Outcome:** `get_pusher_ws_url` moved into `pusher_discovery.py`, which now
  reads constants without a reverse import or cycle.

## Low-priority / opportunistic cleanups

| Smell | Location | Fix |
|---|---|---|
| `# noqa: C901` on large functions | Intrinsic loops such as `twitch/replay_service.py` and `youtube/continuation.py` | Acceptable when documented; extract only genuinely independent helpers |
| `# type: ignore` / `# noqa` annotations | Platform and intrinsically branchy code | Prune stale annotations only alongside related work |

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

## Final disposition

- Safety net, mutable-state cleanup, Kick dependency reversal, facade slimming,
  stateless base utilities, and static model/output decoupling: **complete**.
- Continuation-loop cohesion merge: **complete**; its >400-line allowlist entry
  documents why the behavior stays together.
- Three parsing re-export facades: **complete**; their package surface now lives
  directly in `parsing/__init__.py`.
- Blanket YouTube module consolidation and service/mixin replacement:
  **declined** pending concrete coupling evidence.
- Package file-count/import-depth gates: **declined** because they reward metric
  movement rather than cohesion.
- `Any` baselines: tighten only alongside real typing improvements, following
  `maintenance-backlog.md`; there is no standalone ratchet phase.

## Verification checklist

Changes affecting these areas are not done until:

- `uv run ruff check src/chat_downloader tests` is clean.
- `uv run ruff format --check src/chat_downloader tests` is clean.
- `uv run mypy .` is clean.
- `uv run lint-imports` is clean.
- `uv run pytest -q -p no:rerunfailures -m "not network"` is green.
- User-facing behavior, tooling, project structure, or public API changes are
  documented in the same commit.

---

*Revised 2026-07-12 after codebase audit; retired 2026-07-18 after completion
review. Supersedes `refactor_plan-2026-07-12.md`. Deferrals and evolving notes
belong in
`docs/maintenance-backlog.md` and `docs/maintenance-notes.md`.*

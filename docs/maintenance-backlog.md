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
| `sites/youtube/parsing/message_items_content_parser.py` | 10 | same remap-accumulator pattern as Twitch IRC parsers above — `info: dict[str,Any]` mutated by `r.remap` across 5 helper functions; X9 narrowed the incoming `item`/`item_info` payloads but the accumulator boundary remains; TypedDict declined (V3); do not reopen |
| `sites/youtube/parsing/actions_router.py` | 9 | `_ActionHandler` Callable alias + `ProcessedAction` fields carry assembled message output (`dict[str,Any]`), not raw API payloads; narrowed `action` param to `JSONDict` in X9 but contract/output boundary stays |

---

## Deferred-by-design

All items below have explicit rationale in
[`maintenance-notes.md`](maintenance-notes.md); do not reopen without reading it.

### Cross-site deduplication
**Status: closed** — reopen only if a third site is added.
- **Remapping tables** (`sites/twitch/remappings.py` 326 LOC vs
  `sites/youtube/constants_message.py` 328 LOC): field semantics and
  transformation logic diverge per platform; unification adds indirection without
  code reduction.
- **Badge parsing** (`twitch/parsing/badges.py` vs
  `youtube/parsing/message_content_badges.py`): source payloads are
  incompatible; shared layer would add indirection without reducing logic.

### Intrinsic complexity (noqa: C901)
**Status: closed** — reopen only if a future edit raises the McCabe score above 10.

The four functions below exceed the McCabe-10 gate for legitimate structural
reasons; their `# noqa: C901` annotations carry inline justifications:
- `sites/twitch/live_service.py:iter_stream_chat_messages` — live IRC reconnect
  loop
- `sites/twitch/replay_service.py:iter_vod_chat_messages` — cursor-advance
  guard, first-iteration check, and edge disposition fan-out are intrinsic;
  complexity reduced but remains above gate (see `maintenance-notes.md`)
- `sites/youtube/client_requests_initial.py:_get_initial_info` — HTTP
  status-code dispatch + retry loop; revisit only if a future edit raises its
  McCabe score above 10
- `sites/youtube/chat_streams_runtime_iteration.py:_get_chat_messages` —
  live/replay branching, no-progress guard, and end-message injection are
  intrinsic; complexity reduced but remains above gate (see `maintenance-notes.md`)

### Allowlisted large modules
**Status: closed** — reopen only if a future edit crosses 400 LOC.
- `chat_downloader.py` (416 LOC) — thin API facade; size is docstring-dominated
  (`get_chat` docstring alone is ~90 lines). No split: splitting harms the
  single public entry point and there is no independent logic to move.
- `sites/twitch/extractor.py` (394 LOC) — single-class site extractor,
  maximally delegated; GraphQL glue is `self`-bound. 6 lines under ceiling;
  stable. See `maintenance-notes.md § Round-7`.

### Near-ceiling modules (monitored, not split)
**Status: closed** — reopen only if a module organically crosses the 400-LOC cap.

`sites/twitch/parsing/message_irc_resolve.py` (352),
`sites/twitch/irc_transport.py` (349), `sites/twitch/replay_service.py` (353),
`cli_args.py` (362) — all 340–362 LOC, stable, each declined or a dense-`Any`
parser where split cost ≈ benefit. See `maintenance-notes.md`.

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

### X-series typed-payload migration (X8–X11, 2026-06) — COMPLETE

YouTube (X8–X10) and Twitch (X11) are both migrated off `dict[str, Any]`
payload boundaries to `json_types` aliases. The typed-payload track is
closed for both sites. Per-module before/after tables and residual rationale
live in `maintenance-notes.md § X-series typed-payload migration`. Residuals
in every migrated module are transport objects, callables, public-API params,
or assembled-output accumulators — intentional, do not reopen.

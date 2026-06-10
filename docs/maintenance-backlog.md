# Maintenance Backlog

Single source of truth for ongoing maintainability targets, deferred decisions,
and candidate refactors. See [`maintenance-notes.md`](maintenance-notes.md) for
the full rationale behind deferred items.

## How to use

- **Any-density ratchet**: lower a baseline only after a concrete migration;
  never raise it. After migrating a module, run
  `rg -c "\bAny\b" src/chat_downloader/<path>` to get the new count, then
  update `BASELINE` in `tests/test_any_density_unit.py`.
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

## Active ratchet targets (Any-density)

Modules below their documented stable boundary — next candidates for typing
migration. Technique matched to each module: the accessor pattern
(`get_str`/`get_int`/`get_dict`/`dig` from `utils/json_types.py`) for JSON reads;
concrete `requests.Response` / return-type annotations for HTTP boundaries.

| Module | Current baseline | Priority | Technique |
|--------|-----------------|----------|-----------|
| ~~`sites/base.py`~~ | ~~23~~ → **14** | done (L3c) | concrete HTTP return types; `str\|None` cookie; `re.Match[str]` tuple; `Iterator[str]` generate_urls |
| ~~`sites/session.py`~~ | ~~17~~ → **7** | done (L3a) | `-> requests.Response`/`JSONAny`/`str\|None`; `dict[str,str]` cookie spec |
| `sites/twitch/parsing/messages.py` | 14 | low | All `Any` is accumulator (`info: dict[str,Any]`) + remapping tables — G5 accessor pattern does not reduce these |
| `sites/twitch/parsing/message_irc_resolve.py` | 14 | low | Same — G5d already applied; remainder is accumulator + mutation-only patterns |

**Out of scope** — documented stable boundaries; do not lower without re-reading
`maintenance-notes.md`:

| Module | Baseline | Reason |
|--------|----------|--------|
| `formatting/format.py` | 33 | stable JSON-config ↔ formatter boundary |
| `sites/twitch/remappings.py` | 11 | remapping-table data; see cross-site dedup note |
| `sites/youtube/constants_message.py` | 3 | remapping-table data |

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
- `sites/twitch/replay_service.py:iter_vod_chat_messages` — VOD segment/offset
  edge cases
- `sites/youtube/client_requests_initial.py:_get_initial_info` — HTTP
  status-code dispatch + retry loop
- `sites/youtube/chat_streams_runtime_iteration.py:_get_chat_messages` —
  continuation loop

### Allowlisted large modules
- `utils/timed_utils.py` (422 LOC) — cohesive single-purpose timer/generator
  utilities; no clean split seam.
- `chat_downloader.py` (415 LOC) — thin API facade; intentionally minimal.

---

## Candidate cohesion splits (not yet done)

Lower priority — coupling cost documented below.

### `debugging.py` (355 LOC)
Mixes logging setup, token redaction, testing-mode globals, and colour
detection. **Coupling cost:** `supports_colour()` (`debugging.py:243-292`) feeds
a module-load colorama-init block at `:294` and is exercised by ~15 tests in
`test_debugging_unit.py` and `test_debugging_import_unit.py` — a move would
repoint all patch targets.

### `_ChatOutputDispatcher` in `sites/models.py`
Logically a dispatcher utility, not a data model. **Coupling cost:** its ctor
takes `Chat` directly (would require a `TYPE_CHECKING` forward-ref cycle after
extraction), and `tests/test_chat_models_unit.py` patches
`chat_downloader.sites.models.log` / `…safe_print` — all patch targets would
shift.

# Changelog

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
- Bump the live dedup window and validate the dedup cache limit

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

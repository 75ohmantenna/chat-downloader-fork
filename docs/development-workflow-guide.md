# Development Workflow Guide

Canonical development reference for `chat-downloader-fork`. Covers local setup,
validation, workflow expectations, and architectural guardrails.

This is a personal fork of `xenova/chat-downloader` with no upstream support
commitment. See the [README](../README.md) for the support and AI-assistance
disclosures that apply to all changes in this repository.

## Tooling Baseline

- Python support: 3.12+; CI validates 3.12, 3.13, and 3.14
- Packaging source of truth: `pyproject.toml`
- Test framework: `pytest`
- Formatter and primary linter: `ruff`, configured for an 88-character line
  length (source of truth: `pyproject.toml`)
- Type checker: `mypy` with configuration in `mypy.ini`
- CLI/API parameter source of truth: `src/chat_downloader/models/` package

## Repository Map

| Path | Responsibility |
| --- | --- |
| `src/chat_downloader/chat_downloader.py` | Thin public facade, `ChatDownloader`, `run()` |
| `src/chat_downloader/cli.py` | Argparse CLI generated from dataclass metadata plus CLI-only flags |
| `src/chat_downloader/models/` | `DownloaderConfig`, `ChatRequest`, `RunConfig`, CLI metadata |
| `src/chat_downloader/runtime/cli_bridge.py` | Split `run()` kwargs into init, chat-request, and runtime controls |
| `src/chat_downloader/runtime/site_dispatch.py` | URL validation, site matching, site-default resolution |
| `src/chat_downloader/runtime/chat_pipeline.py` | Message limits, timeouts, formatters, output writers |
| `src/chat_downloader/runtime/runner.py` | CLI-style execution loop, stdout callback, cleanup, error mapping |
| `src/chat_downloader/runtime/session_lifecycle.py` | Session creation, cookies, shared site session cleanup |
| `src/chat_downloader/sites/base.py` | Shared site-session base behavior |
| `src/chat_downloader/sites/session.py` | Request sessions, cookies, headers, profiles |
| `src/chat_downloader/sites/retry.py` | Shared retry helper |
| `src/chat_downloader/sites/filters.py` | Message and time-range filters |
| `src/chat_downloader/sites/youtube/` | YouTube bootstrap, continuation, discovery, parsing |
| `src/chat_downloader/sites/twitch/` | Twitch GraphQL, IRC, replay, badges, parsing |
| `src/chat_downloader/output/` | Continuous writers and crash-safe JSONL handling |
| `src/chat_downloader/formatting/` | Text formatting engine and bundled format definitions |
| `src/chat_downloader/debugging.py` | Logging, sanitization, testing modes, opt-in debug sample capture |
| `src/chat_downloader/debug_sample_utils.py` | Debug sample naming and fixture hint helpers |
| `tests/fixtures/` | Curated offline fixtures |

## Local Setup

Install the project and all development dependencies, then wire up the Git hooks:

```bash
uv sync
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
```

Or equivalently: `make setup` runs both steps.

## Git Hooks

The repository ships a `.pre-commit-config.yaml` with two stages:

- **pre-commit** — `ruff check` and `ruff format --check` run on every commit.
- **pre-push** — `mypy .` runs before each push (slower; skipped on the
  commit itself).

Hooks use `uv run --locked` so they always match the pinned `uv.lock` versions.
To run all hooks manually against the full tree: `uv run pre-commit run
--all-files`.

## Daily Workflow

Local iteration loop:

1. Make the code or documentation change.
2. Run the offline test path (see Common Commands below).
3. Run formatting, lint, and type checks if the change touches Python code.
4. Update the relevant focused docs when behavior or public API changes.

## Common Commands

Run the default offline test suite:

```bash
uv run pytest -q -p no:rerunfailures -m "not network"
```

Run formatting and lint checks:

```bash
uv run ruff check src/chat_downloader tests
uv run ruff format --check src/chat_downloader tests
```

Run type checking:

```bash
uv run mypy .
```

Run the optional spelling check, using the repository's `pyproject.toml`
configuration:

```bash
codespell
```

Run coverage locally:

```bash
uv run coverage erase
PYTHONHASHSEED=0 uv run coverage run --source chat_downloader -m pytest -q -m "not network"
uv run coverage report -m --precision=2
```

### Using Make

The project `Makefile` wraps the commands above into convenient targets:

| Target | Equivalent to |
| --- | --- |
| `make setup` | install Git hooks, then `uv sync` |
| `make lock` | `uv lock` (update the lockfile) |
| `make lock-check` | `uv lock --check` (verify the lockfile is current) |
| `make test` | pytest offline suite |
| `make lint` | ruff check |
| `make fmt` | ruff format (apply) |
| `make fmt-check` | ruff format --check |
| `make typecheck` | mypy |
| `make coverage` | coverage erase + run + report, **enforced at 100% line coverage** |
| `make build` | clean `dist/` then build wheel and sdist |
| `make smoke` | build, then install the wheel in an isolated env and run `chat_downloader --version` |
| `make check` | lint + fmt-check + typecheck + test (fast local loop) |
| `make ci` | **canonical validation** — used locally and in GitHub Actions |
`make ci` is the single source of truth for validation. It runs the full
deterministic offline path: `lock-check` → `lint` → `fmt-check` → `typecheck`
→ `coverage` (enforced at 100% line coverage) → `smoke` (which builds first). GitHub Actions
runs this exact target after `uv sync --locked`, so local and hosted CI cannot
drift.

The canonical `make ci` path runs validation tools through `uv run --locked`
(the `UV_RUN` Makefile variable), so the committed lockfile is always honored.
The convenience targets `make test`, `make fmt`, and `make lock` intentionally
remain unlocked for fast local iteration.

Line coverage is enforced at 100% via `fail_under = 100` in
`[tool.coverage.report]` in `pyproject.toml`; `make coverage` (and therefore
`make ci`) fails if total coverage drops below 100%.

## Test Strategy

The repository defaults to deterministic offline validation.

Markers in use:

- `@pytest.mark.network`: tests that require external network access

Prefer the offline suite for normal iteration. Run network tests only when
validating platform behavior that cannot be covered by fixtures.

Coverage reproducibility notes:

- Run `coverage erase` before each test run
- Set `PYTHONHASHSEED=0` on coverage runs for deterministic output
- Use `coverage report --precision=2` for stable numeric output

## Architecture Guardrails

- Treat `src/chat_downloader/chat_downloader.py` as the public facade
- Add CLI/API parameters to `DownloaderConfig`, `ChatRequest`, or `RunConfig`
  in `models/` before wiring them elsewhere
- Prefer `DownloaderConfig`, `ChatRequest`, and `RunConfig` over ad hoc
  parameter paths
- Keep `run()` keyword categorization in `runtime/cli_bridge.py`; unknown
  `run()` kwargs should fail fast instead of being silently ignored
- Keep runtime orchestration in `src/chat_downloader/runtime/`; avoid pushing
  output, timeout, run-loop, or URL-dispatch behavior into site modules
- Keep site-specific behavior inside the YouTube, Twitch, and Kick site
  packages
- Prefer focused utility modules under `src/chat_downloader/utils/`
  (e.g. `time_utils.py`, `json_utils.py`, `string_utils.py`) over adding
  broad utility or compatibility-style aggregator modules
- Use current Twitch boundaries: `graphql_client.py`, `irc_transport.py`,
  `live_service.py`, `replay_service.py`, `replay_transport.py`,
  `remappings.py`, and `parsing/`
- Use current YouTube boundaries: `video_initialization.py`,
  `client_context.py`, `client_requests_initial.py`,
  `client_requests_continuation.py`, `continuations.py`,
  `continuation.py`, `continuation_helpers.py`, `message_pipeline.py`, and
  `parsing/`
- Use current Kick boundaries: `api_client.py`, `websocket_transport.py`,
  `live_service.py`, `replay_service.py`, `constants.py`, and `parsing/`

## Documentation Maintenance

When behavior changes, update the focused document in the same change.

Common doc targets:

- `README.md`: user-facing overview, install, quick-start, docs map
- `docs/capability-inventory.md`: capability-preservation checklist
- `docs/python-api-reference.md`: public Python API and dataclass reference
- `docs/youtube-integration-guide.md`: YouTube capture flow and module guide
- `docs/twitch-integration-guide.md`: Twitch capture flow and module guide
- `AGENTS.md` and `CLAUDE.md`: agent workflow notes

When the public import surface changes, keep these files aligned:

- `src/chat_downloader/__init__.py`
- package `__init__.py` files under `src/chat_downloader/`
- `docs/python-api-reference.md`
- import-surface tests under `tests/`

Do not promote internal helpers to top-level exports unless they are intended
to be stable consumer-facing API.

## Output and Debugging Notes

- `jsonl` is the safest format for live or long-running captures
- JSON-array `.json` output is not supported; use `.jsonl` for structured
  output
- Use `--logging debug` or `--verbose` when investigating parser or transport
  issues
- Use `--testing` for debug logging plus `pause_on_debug`
- `debug_log()` is for unexpected data-quality conditions and is intentionally
  noisier than normal debug logging
- Keep stable regression fixtures in `tests/fixtures/`

### Debug Sample Capture

Parser debugging can capture sanitized payloads for later fixture promotion.
Capture is opt-in and only active when debug logging is enabled:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" --logging debug
```

Captured files land in a temp directory and use stable labels from
`src/chat_downloader/debug_sample_utils.py`. Override the output directory with
`CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR`. Review generated files before promoting
them into `tests/fixtures/`.

### Twitch drift fix workflow

When a live IRC message or GraphQL shape triggers `debug_log` with an unknown
type or unrecognized action:

1. **Reproduce** — run the failing stream with
   `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1` and `--logging debug` to confirm
   the snapshot is written to `tests/fixtures/twitch/debug_samples/`.
2. **Identify** — read the snapshot. The log prefix `Unknown action type` or
   `Unknown message type` points to `parsing/message_irc_resolve.py`; an
   unexpected GraphQL shape points to `graphql_client.py`.
3. **Fix** — in order of what the message likely needs:
   - New IRC action type → extend the dispatch map in
     `parsing/message_irc_resolve.py::_resolve_irc_action_and_message_type`.
   - New IRC message type → extend
     `parsing/message_irc_resolve.py::_resolve_message_type`.
   - New IRC tag → add to the tag-decoding logic in `parsing/tag_decoding.py`
     and extend the known-key set in `validation_keys.py`.
   - GraphQL hash rotation → update `OPERATION_HASHES` in `constants.py`;
     the guard test `test_operation_hashes_covers_all_used_operations`
     (in `tests/test_twitch_drift_harness_unit.py`) fails immediately if
     a hash entry is missing.
4. **Capture raw IRC line** — grab the verbatim IRC message that triggered
   the drift. Add a `{"raw": "<irc line>\\r\\n"}` fixture to
   `tests/fixtures/twitch/live_events/` with a descriptive name.
5. **Validate** — run the Twitch drift harness:
   ```bash
   uv run pytest -q tests/test_twitch_drift_harness_unit.py
   ```
   It replays every fixture and asserts neither `"Unknown action type"` nor
   `"Unknown message type"` sentinel fires. A passing harness means the fix is
   a permanent regression anchor.
6. **Full suite** — run `make ci` before committing.

## Version Bumps

1. Update `src/chat_downloader/metadata.py` — change `__version__`.
2. Add a new topmost numbered release entry to `CHANGELOG.md`; the heading
   must match the new version exactly (`## X.Y.Z — YYYY-MM-DD`).
3. Leave `pyproject.toml` unchanged — setuptools loads the version dynamically
   from `chat_downloader.metadata.__version__`.
4. Validate:
   ```bash
   uv lock
   uv lock --check
   uv sync --locked
   uv run --locked pytest -q -p no:rerunfailures -m "not network"
   uv build
   ```
5. Inspect wheel metadata and confirm the expected version:
   ```bash
   unzip -p dist/chat_downloader-*.whl '*.dist-info/METADATA' | grep '^Version:'
   ```

The offline test `tests/test_release_metadata_unit.py` fails if the topmost
numbered changelog heading does not match the package version.
All five steps and the passing test must land in one commit.

## GitHub Actions CI

GitHub Actions is the repository's supported hosted CI platform.

Workflow file: `.github/workflows/ci.yml`

CI validates Python: 3.12, 3.13, and 3.14.

CI runs on pushes to **all branches**. Pull requests targeting `master` are
also validated. Manual runs are available via `workflow_dispatch` from the
GitHub Actions interface.

The workflow installs dependencies with `uv sync --locked`, then runs the
canonical `make ci` target. It declares read-only `contents` permission,
cancels superseded in-progress runs for the same ref via `concurrency`, and
sets a per-job `timeout-minutes`.

Network tests are opt-in and excluded from the default CI suite (`-m "not network"`).

Do not add Gitea, Forgejo, Codeberg, or Woodpecker CI workflows to this repository.

## Deep Ruff Passes

The normal workflow uses the curated Ruff rule set in `pyproject.toml`.
For one-off cleanup or investigation, stricter repo-wide passes can be run
directly.

Deep lint check:

```bash
uv run ruff check . --select ALL && uv run ruff format --check .
```

Deep format and fix:

```bash
uv run ruff format . && uv run ruff check . --select ALL --fix --unsafe-fixes && uv run ruff format .
```

These are intentionally more aggressive and may surface far more noise than
the normal project lint path.

## Maintenance Notes

Design decisions, deferred refactors, and non-obvious architectural choices
that affect day-to-day development are documented in
[`docs/maintenance-notes.md`](maintenance-notes.md).

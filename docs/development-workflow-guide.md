# Development Workflow Guide

Canonical development reference for `chat-downloader-fork`. Covers local setup,
validation, workflow expectations, and architectural guardrails.

This is a personal fork of `xenova/chat-downloader` with no upstream support
commitment. See the [README](../README.md) for the support and AI-assistance
disclosures that apply to all changes in this repository.

## Tooling Baseline

- Python support: 3.12+
- Packaging source of truth: `pyproject.toml`
- Test framework: `pytest`
- Formatter and primary linter: `ruff`
- Type checker: `mypy` with configuration in `mypy.ini`
- CLI/API parameter source of truth: `chat_downloader/models.py`

## Repository Map

| Path | Responsibility |
| --- | --- |
| `chat_downloader/chat_downloader.py` | Thin public facade, `ChatDownloader`, `run()` |
| `chat_downloader/cli.py` | Argparse CLI generated from dataclass metadata plus CLI-only flags |
| `chat_downloader/models.py` | `DownloaderConfig`, `ChatRequest`, `RunConfig`, CLI metadata |
| `chat_downloader/runtime/cli_bridge.py` | Split `run()` kwargs into init, chat-request, and runtime controls |
| `chat_downloader/runtime/site_dispatch.py` | URL validation, site matching, site-default resolution |
| `chat_downloader/runtime/chat_pipeline.py` | Message limits, timeouts, formatters, output writers |
| `chat_downloader/runtime/runner.py` | CLI-style execution loop, stdout callback, cleanup, error mapping |
| `chat_downloader/runtime/session_lifecycle.py` | Session creation, cookies, shared site session cleanup |
| `chat_downloader/sites/base.py` | Shared site-session base behavior |
| `chat_downloader/sites/session.py` | Request sessions, cookies, headers, profiles |
| `chat_downloader/sites/retry.py` | Shared retry helper |
| `chat_downloader/sites/filters.py` | Message and time-range filters |
| `chat_downloader/sites/youtube/` | YouTube bootstrap, continuation, discovery, parsing |
| `chat_downloader/sites/twitch/` | Twitch GraphQL, IRC, replay, badges, parsing |
| `chat_downloader/output/` | Continuous writers and crash-safe JSONL handling |
| `chat_downloader/formatting/` | Text formatting engine and bundled format definitions |
| `chat_downloader/debugging.py` | Logging, sanitization, testing modes, opt-in debug sample capture |
| `chat_downloader/debug_sample_utils.py` | Debug sample naming and fixture hint helpers |
| `tests/fixtures/` | Curated offline fixtures |

## Local Setup

Install the project in editable mode with development dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Daily Workflow

Local iteration loop:

1. Make the code or documentation change.
2. Run the offline test path (see Common Commands below).
3. Run formatting, lint, and type checks if the change touches Python code.
4. Update the relevant focused docs when behavior or public API changes.

## Common Commands

Run the default offline test suite:

```bash
python3 -m pytest -q -p no:rerunfailures -m "not network"
```

Run formatting and lint checks:

```bash
python3 -m ruff check chat_downloader tests
python3 -m ruff format --check chat_downloader tests
```

Run type checking:

```bash
python3 -m mypy .
```

Run coverage locally:

```bash
python3 -m coverage erase
PYTHONHASHSEED=0 python3 -m coverage run --source chat_downloader -m pytest -q -m "not network"
python3 -m coverage report -m --precision=2
```

### Using Make

The project `Makefile` wraps the commands above into convenient targets:

| Target | Equivalent to |
| --- | --- |
| `make setup` | Create `.venv` and install dev deps |
| `make test` | pytest offline suite |
| `make lint` | ruff check |
| `make fmt` | ruff format (apply) |
| `make fmt-check` | ruff format --check |
| `make typecheck` | mypy |
| `make check` | lint + fmt-check + typecheck + test |

Each target auto-bootstraps `.venv` if it is absent.

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

- Treat `chat_downloader/chat_downloader.py` as the public facade
- Add CLI/API parameters to `DownloaderConfig`, `ChatRequest`, or `RunConfig`
  before wiring them elsewhere
- Prefer `DownloaderConfig`, `ChatRequest`, and `RunConfig` over ad hoc
  parameter paths
- Keep `run()` keyword categorization in `runtime/cli_bridge.py`; unknown
  `run()` kwargs should fail fast instead of being silently ignored
- Keep runtime orchestration in `chat_downloader/runtime/`; avoid pushing
  output, timeout, run-loop, or URL-dispatch behavior into site modules
- Keep site-specific behavior inside the YouTube and Twitch site packages
- Prefer focused utility modules under `chat_downloader/utils/`
  (e.g. `time_utils.py`, `json_utils.py`, `string_utils.py`) over adding
  broad utility or compatibility-style aggregator modules
- Use current Twitch boundaries: `graphql_client.py`, `irc_transport.py`,
  `live_service.py`, `replay_service.py`, `replay_transport.py`,
  `remappings.py`, and `parsing/`
- Use current YouTube boundaries: `video_initialization.py`,
  `client_context.py`, `client_requests_initial.py`,
  `client_requests_continuation.py`, `continuations.py`,
  `chat_streams_runtime_iteration.py`, `message_pipeline.py`, and `parsing/`

## Documentation Maintenance

When behavior changes, update the focused document in the same change.

Common doc targets:

- `README.md`: user-facing overview, install, quick-start, docs map
- `docs/python-api-reference.md`: public Python API and dataclass reference
- `docs/youtube-integration-guide.md`: YouTube capture flow and module guide
- `docs/twitch-integration-guide.md`: Twitch capture flow and module guide
- `AGENTS.md` and `CLAUDE.md`: agent workflow notes

When the public import surface changes, keep these files aligned:

- `chat_downloader/__init__.py`
- package `__init__.py` files under `chat_downloader/`
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
`chat_downloader/debug_sample_utils.py`. Override the output directory with
`CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR`. Review generated files before promoting
them into `tests/fixtures/`.

## Deep Ruff Passes

The normal workflow uses the curated Ruff rule set in `pyproject.toml`.
For one-off cleanup or investigation, stricter repo-wide passes can be run
directly.

Deep lint check:

```bash
python3 -m ruff check . --select ALL && python3 -m ruff format --check .
```

Deep format and fix:

```bash
python3 -m ruff format . && python3 -m ruff check . --select ALL --fix --unsafe-fixes && python3 -m ruff format .
```

These are intentionally more aggressive and may surface far more noise than
the normal project lint path.

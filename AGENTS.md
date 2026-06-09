# Repository Guidelines

YouTube and Twitch livestream chat CLI plus typed Python API. Python 3.12+; CI validates 3.12, 3.13, and 3.14.
Personal fork of `xenova/chat-downloader`; no upstream support. See README.
For deeper context see [`docs/development-workflow-guide.md`](docs/development-workflow-guide.md).

## Structure
- `src/chat_downloader/`: package. Thin facade `chat_downloader.py`; CLI in `cli.py`; typed shapes in `models/` package.
- `src/chat_downloader/runtime/`: `cli_bridge`, `site_dispatch`, `chat_pipeline`, `runner`, `session_lifecycle`, `testing`.
- `src/chat_downloader/sites/`: shared `base`, `session`, `retry`, `filters`, `models`, `remap`; per-site packages `youtube/` and `twitch/` (each with `parsing/`).  Twitch `parsing/` contains `messages` (entry points), `message_emotes` (emote/image helpers), `message_irc_resolve` (IRC type/action/room-state resolution), `badges`, and `tag_decoding`.
- `src/chat_downloader/output/`: `ContinuousWriter` plus JSON-lines, CSV, and text writer subclasses.
- `src/chat_downloader/formatting/`: `ItemFormatter` and bundled `custom_formats.json`.
- `src/chat_downloader/utils/`: focused helpers (`time_utils`, `json_utils`, `string_utils`, `retry_utils`, `timed_utils`, `dict_utils`, `conversion_utils`, `color_utils`, `console_utils`).
- `tests/`: pytest suite with curated fixtures under `tests/fixtures/`; network tests gated by `@pytest.mark.network`.
- `docs/`: `cli-usage.md`, `development-workflow-guide.md`, `python-api-reference.md`, and the YouTube/Twitch integration guides.

## Architecture
- `models/`: `DownloaderConfig` (`_config.py`), `ChatRequest` (`_request.py`), `RunConfig` + `coerce_chat_request` (`_runconfig.py`); shared helpers in `_base.py`; `__init__.py` is the single public import surface — `from chat_downloader.models import ...` is unchanged
- `runtime/`: `cli_bridge.py` (strict `run()` param categorization), `site_dispatch.py` (URL→site + site defaults), `chat_pipeline.py` (limits/timeouts/format/output), `runner.py` (run loop + cleanup), `session_lifecycle.py` (cookies/sessions/cookie-domain validation), `testing.py`
- `sites/`: `base.py`, `session.py` (proxy URL validation), `retry.py`, `filters.py` (message group validation), `models.py`, `remap.py`
- `output/`: `ContinuousWriter`, JSON-lines/CSV/text writer subclasses
- `formatting/`: `ItemFormatter` and bundled `custom_formats.json`
- `debugging.py`: logging and testing modes, sanitization, opt-in debug sample capture; `debug_sample_utils.py`: fixture naming hints
- `tests/fixtures/`: curated parser, error, and live-event fixtures

## Platforms
- YouTube: watch-page bootstrap + chat-page continuation recovery + InnerTube polling; auth via cookies and SAPISIDHASH; profile fallback in continuation loop.
- Twitch: live = IRC; VOD, clips, metadata = GraphQL persisted queries; badge state is instance-owned. Hash rotation breaks `graphql_client.py` and `constants.py` first.

## Output formats
- `jsonl` / `csv` / `txt`. JSON-array `.json` output is not supported.

## Environment setup
```
uv sync
```

Run `make setup` after cloning to also install Git hooks (`pre-commit` runs
ruff on commit, `mypy` on push). See `docs/development-workflow-guide.md` for
details.

## Commands
- `uv run pytest -q -p no:rerunfailures -m "not network"` — offline test suite (default loop)
- `uv run pytest tests/FILE.py -q` — single file
- `uv run pytest tests/FILE.py::test_name -q` — single test
- `uv run pytest -v -m network --run-network` — opt-in network tests
- `uv run ruff check src/chat_downloader tests` — lint
- `uv run ruff format --check src/chat_downloader tests` — format check
- `uv run ruff format src/chat_downloader tests` — apply formatting
- `uv run mypy .` — type check (config in `mypy.ini`)
- Coverage: `make coverage` — deterministic offline suite with the 100% threshold enforced from `pyproject.toml`.
- Or via `make`: `make setup` (bootstrap), `make test`, `make lint`, `make fmt`, `make fmt-check`, `make typecheck`, `make check` (fast local loop).
- `make ci` — canonical validation (lock-check, lint, fmt-check, typecheck, coverage at 100%, build, smoke); the same target GitHub Actions runs.
- `make lock-check` (`uv lock --check`) and `make smoke` (install built wheel in an isolated env, run `chat_downloader --version`).
- `make complexity` — scan for functions above the aspirational mccabe threshold of 8 (CI gate is 10; this target is advisory, not blocking).

## Style
- Python 3.12+ (CI validates 3.12, 3.13, and 3.14). Ruff formatter, 80-char lines, double quotes.
- Types: `DownloaderConfig`/`ChatRequest`/`RunConfig`; the `models/` package
  is canonical and the source of truth for CLI and Python API shape. Add
  user-facing request, init, or runtime fields there first so CLI help and
  the typed API stay aligned.
- `src/chat_downloader/chat_downloader.py` is a thin facade — keep it that way;
  runtime orchestration lives in `runtime/`.
- Site logic lives in `sites/youtube/` and `sites/twitch/`.
- CLI help is generated from dataclass metadata; change the dataclass first.
- Prefer focused modules over broad utility or compatibility-style imports.
- Test file naming: `test_<behavior>.py` or `test_<area>_unit.py`.

## Testing
Default to the offline suite (`-m "not network"`). Mark live-network tests
`@pytest.mark.network`. Add regression tests for any parser, retry, output,
or runtime change. Keep curated fixtures under `tests/fixtures/`.

## Done means
A behavior, runtime, or tooling change is not done until:
- regression test added or updated under `tests/` (curated fixture if parser)
- `uv run ruff check src/chat_downloader tests` clean
- `uv run ruff format --check src/chat_downloader tests` clean
- `uv run mypy .` clean
- `uv run pytest -q -p no:rerunfailures -m "not network"` green
- docs touched in the same commit when user-facing behavior, tooling, or
  project structure changed
- every version bump updates `CHANGELOG.md` in the same commit; the topmost
  numbered release heading must match `src/chat_downloader/metadata.py::__version__`

## Debug
- `--logging debug` or `--verbose` for parser and transport issues
- `--testing` means debug logging plus pause-on-debug
- `debug_log()` in `debugging.py` is for unexpected data-quality conditions only

## Commits
- Subject: `[topic] short imperative summary`. Topic is one of `build`,
  `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`,
  `style`, or `test`. Aim for ~50 chars; 72 max. No trailing period.
- Body (when needed): `- ` bullets, one per logical change, no prose
  paragraphs, wrap at 72 columns.

## CI

GitHub Actions is the only supported hosted CI platform. Workflow file:
`.github/workflows/ci.yml`. Preserve:

- Push coverage for all branches
- Pull-request coverage targeting `master`
- `workflow_dispatch` for manual runs
- Python matrix: `["3.12", "3.13", "3.14"]`
- uv-based install (`uv sync --locked`), then the canonical `make ci` target
- Read-only `contents` permission, `concurrency` cancellation, and a job
  `timeout-minutes`
- Network tests opt-in only (`-m "not network"` is the default)

Do not add Gitea, Forgejo, Codeberg, or Woodpecker CI configuration.

## Agent Notes
- Prefer current module boundaries over broad legacy-style helpers.
- Behavior, tooling, and structural changes ship with their doc updates in the same commit.
- README edits are limited to user-facing summaries.
- `CHANGELOG.md` entries summarize user-visible, tooling, compatibility, and
  structural changes; do not rewrite historical release entries.
- This is a personal fork with no upstream support. Do not file issues or
  PRs against [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader)
  for problems originating in this fork.

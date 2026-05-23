# Repository Guidelines

Quick reference for coding agents. For deeper context see
[`CLAUDE.md`](CLAUDE.md) and [`docs/development-workflow-guide.md`](docs/development-workflow-guide.md).

## Structure
- `chat_downloader/`: package. Thin facade `chat_downloader.py`; CLI in `cli.py`; typed shapes in `models.py`.
- `chat_downloader/runtime/`: `cli_bridge`, `site_dispatch`, `chat_pipeline`, `runner`, `session_lifecycle`, `testing`.
- `chat_downloader/sites/`: shared `base`, `session`, `retry`, `filters`, `models`, `remap`; per-site packages `youtube/` and `twitch/` (each with `parsing/`).
- `chat_downloader/output/`: `ContinuousWriter` plus jsonl/csv/txt writers; `formatting/`: `ItemFormatter` and bundled templates.
- `chat_downloader/utils/`: focused helpers (`time_utils`, `json_utils`, `string_utils`, `retry_utils`, `timed_utils`, `dict_utils`, `conversion_utils`, `color_utils`, `console_utils`).
- `tests/`: pytest suite with curated fixtures under `tests/fixtures/`; network tests gated by `@pytest.mark.network`.
- `docs/`: `development-workflow-guide.md`, `python-api-reference.md`, and the YouTube/Twitch integration guides.

## Environment setup
```
python3 -m venv .venv
.venv/bin/pip3 install -e ".[dev]"
```
All commands below assume the venv exists. Activate with
`source .venv/bin/activate`, or call the binaries directly as shown.

## Commands
- `.venv/bin/python3 -m pytest -q -p no:rerunfailures -m "not network"` — offline test suite (default loop)
- `.venv/bin/python3 -m pytest tests/FILE.py -q` — single file
- `.venv/bin/python3 -m pytest -v -m network` — opt-in network tests
- `.venv/bin/python3 -m ruff check chat_downloader tests` — lint
- `.venv/bin/python3 -m ruff format --check chat_downloader tests` — format check
- `.venv/bin/python3 -m ruff format chat_downloader tests` — apply formatting
- `.venv/bin/python3 -m mypy .` — type check (config in `mypy.ini`)
- Coverage: `.venv/bin/python3 -m coverage erase && PYTHONHASHSEED=0 .venv/bin/python3 -m coverage run --source chat_downloader -m pytest -q -m "not network" && .venv/bin/python3 -m coverage report -m --precision=2`

## Style
- Python 3.12+. Ruff formatter, 80-char lines, double quotes.
- `models.py` is the source of truth for CLI and Python API shape. Add
  user-facing request, init, or runtime fields there first so CLI help and
  the typed API stay aligned.
- Use the typed flow: `DownloaderConfig`, `ChatRequest`, `RunConfig`. Keep
  `chat_downloader.py` a thin facade and runtime orchestration in `runtime/`.
- Test file naming: `test_<behavior>.py` or `test_<area>_unit.py`.

## Testing
Default to the offline suite (`-m "not network"`). Mark live-network tests
`@pytest.mark.network`. Add regression tests for any parser, retry, output,
or runtime change. Keep curated fixtures under `tests/fixtures/`.

## Commits
- Subject: `[topic] short imperative summary`. Topic is one of `build`,
  `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`,
  `style`, or `test`. Aim for ~50 chars; 72 max. No trailing period.
- Body (when needed): `- ` bullets, one per logical change, no prose
  paragraphs, wrap at 72 columns.

## Agent Notes
- Prefer current module boundaries over broad legacy-style helpers. The
  authoritative boundary list lives in [`CLAUDE.md`](CLAUDE.md) and `docs/`.
- Behavior changes ship with their doc updates in the same commit.
- README edits are limited to user-facing summaries.
- This is a personal fork with no upstream support. Do not file issues or
  PRs against [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader)
  for problems originating in this fork.

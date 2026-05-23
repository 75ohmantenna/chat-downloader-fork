YouTube and Twitch livestream chat CLI plus typed Python API. Python 3.12+.
Personal fork of `xenova/chat-downloader`; no upstream support. See README.

## Style
- Types: `DownloaderConfig`/`ChatRequest`/`RunConfig`; `models.py` is canonical.
- `chat_downloader/chat_downloader.py` is a thin facade — keep it that way.
- Site logic lives in `sites/youtube/` and `sites/twitch/`.
- CLI help is generated from dataclass metadata; change the dataclass first.
- Prefer focused modules over broad utility or compatibility-style imports.

## Architecture
- `models.py`: `DownloaderConfig`, `ChatRequest`, `RunConfig`, CLI metadata
- `runtime/`: `cli_bridge.py` (strict `run()` param categorization), `site_dispatch.py` (URL→site + site defaults), `chat_pipeline.py` (limits/timeouts/format/output), `runner.py` (run loop + cleanup), `session_lifecycle.py` (cookies/sessions/cookie-domain validation), `testing.py`
- `sites/`: `base.py`, `session.py` (proxy URL validation), `retry.py`, `filters.py` (message group validation), `models.py`, `remap.py`
- `output/`: `ContinuousWriter`, JSONL/CSV/TXT writers; `formatting/`: `ItemFormatter`
- `debugging.py`: logging and testing modes, sanitization, opt-in debug sample capture; `debug_sample_utils.py`: fixture naming hints
- `tests/fixtures/`: curated parser, error, and live-event fixtures

## Platforms
- YouTube: watch-page bootstrap + chat-page continuation recovery + InnerTube polling; auth via cookies and SAPISIDHASH; profile fallback in continuation loop.
- Twitch: live = IRC; VOD, clips, metadata = GraphQL persisted queries; badge state is instance-owned. Hash rotation breaks `graphql_client.py` and `constants.py` first.

## Output formats
- `jsonl` / `csv` / `txt`. JSON-array `.json` output is not supported.

## Debug
- `--logging debug` or `--verbose` for parser and transport issues
- `--testing` means debug logging plus pause-on-debug
- `debug_log()` in `debugging.py` is for unexpected data-quality conditions only

## Commits
- Subject: `[topic] short description` — topic is one of `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, or `test`.
- Subject: aim for ~50 chars; 72 max only if unavoidable. No trailing period.
- Body (when needed): `- ` bullet per logical change, no prose paragraphs, wrap at 72 cols.

## Environment setup
```
python3 -m venv .venv
.venv/bin/pip3 install -e ".[dev]"
```
All commands below assume the venv exists. Activate with
`source .venv/bin/activate`, or call binaries directly via `.venv/bin/`.

## Validate
```
.venv/bin/python3 -m pytest -q -p no:rerunfailures -m "not network"
.venv/bin/python3 -m ruff check chat_downloader tests
.venv/bin/python3 -m ruff format --check chat_downloader tests
.venv/bin/python3 -m mypy .
```
Single test: `.venv/bin/python3 -m pytest tests/FILE.py::test_name -q`.
Coverage: `.venv/bin/python3 -m coverage erase && PYTHONHASHSEED=0 .venv/bin/python3 -m coverage run --source chat_downloader -m pytest -q -m "not network" && .venv/bin/python3 -m coverage report -m --precision=2`.

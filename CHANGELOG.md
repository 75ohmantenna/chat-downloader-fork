# Changelog

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

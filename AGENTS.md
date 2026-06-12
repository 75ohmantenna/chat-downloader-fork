# Repository Guidelines

YouTube and Twitch livestream chat CLI plus typed Python API. Python 3.12+;
CI validates 3.12, 3.13, and 3.14. This is a personal fork of
`xenova/chat-downloader`; no upstream support is offered. Do not file issues or
PRs against upstream for fork-originating problems.

For deeper context use:

- [`docs/architecture.md`](docs/architecture.md) — layer diagram, module
  inventory, guardrails
- [`docs/capability-inventory.md`](docs/capability-inventory.md) — behavior
  preservation checklist
- [`docs/development-workflow-guide.md`](docs/development-workflow-guide.md) —
  local workflow and validation
- [`docs/maintenance-backlog.md`](docs/maintenance-backlog.md) and
  [`docs/maintenance-notes.md`](docs/maintenance-notes.md) — live deferrals and
  design-decision record

## Branch Safety

- Check `git status --short --branch` before editing.
- Work on the requested branch; if the task names a branch, create or switch to
  it before changing files.
- Preserve user changes. Do not revert unrelated dirty files.
- Do not weaken guardrail thresholds or reopen closed deferrals without
  documented evidence.

## Architecture Rules

- `src/chat_downloader/chat_downloader.py` is a thin public facade; runtime
  orchestration belongs in `src/chat_downloader/runtime/`.
- `src/chat_downloader/models/` is the canonical typed shape for init, request,
  and run parameters. Add user-facing request/init/runtime fields there first
  so CLI help and the typed API stay aligned.
- CLI help is generated from dataclass metadata; change dataclasses before
  parser wiring.
- Keep YouTube and Twitch behavior inside their site packages. Do not add
  cross-site abstractions for remapping, badges, retry, or parser logic unless
  a real third-site or maintenance-pressure case exists.
- Use `utils/json_types` accessors (`get_str`, `get_int`, `get_dict`,
  `get_list`, `dig`) for incoming platform JSON. Avoid annotating raw payloads
  as `Any`; reserve `dict[str, Any]` for heterogeneous accumulator dicts.
- Output formats are `jsonl`, `csv`, and `txt`. JSON-array `.json` output is
  not supported.

## Commands

- `uv sync` — install dependencies
- `make setup` — install Git hooks, then `uv sync`
- `uv run pytest -q -p no:rerunfailures -m "not network"` — offline suite
- `uv run pytest tests/FILE.py -q` — single file
- `uv run pytest tests/FILE.py::test_name -q` — single test
- `uv run pytest -v -m network --run-network` — opt-in network tests
- `uv run ruff check src/chat_downloader tests` — lint
- `uv run ruff format --check src/chat_downloader tests` — format check
- `uv run mypy .` — type check
- `make ci` — canonical validation: lock-check, lint, fmt-check, typecheck,
  100% offline coverage, build, smoke

## Testing

- Default to offline tests. Mark live-network tests with `@pytest.mark.network`.
- Add regression tests for parser, retry, output, runtime, public API, or
  tooling changes.
- Add or promote a fixture before reshaping parser behavior.
- Keep curated fixtures under `tests/fixtures/`.
- Coverage pragmas are only for defensive or unreachable branches and must
  include a one-line reason.

Key ratchets:

| Test file | What it guards |
| --- | --- |
| `tests/test_facade_param_sync_unit.py` | `get_chat()` params stay in sync with `ChatRequest` |
| `tests/test_any_density_unit.py` | Per-module `Any` counts do not regress |
| `tests/test_module_size_unit.py` | Non-allowlisted modules stay under 400 lines |
| `tests/test_public_api_unit.py` | Public import surfaces stay intentional |
| `tests/test_makefile_contract_unit.py` | Canonical Makefile validation target stays pinned |

## Style

- Every source file starts with `from __future__ import annotations`.
- Ruff formatter, 88-character lines, double quotes.
- Type-only imports go in `if TYPE_CHECKING:` blocks.
- Mccabe complexity gate is 10. Intrinsically branchy functions may carry
  `# noqa: C901` only with a short rationale comment.
- Prefer focused modules over broad compatibility helpers.
- Test files are named `test_<behavior>.py` or `test_<area>_unit.py`.

## Done Means

A behavior, runtime, parser, or tooling change is not done until:

- Regression test added or updated under `tests/`
- `uv run ruff check src/chat_downloader tests` clean
- `uv run ruff format --check src/chat_downloader tests` clean
- `uv run mypy .` clean
- `uv run pytest -q -p no:rerunfailures -m "not network"` green
- Docs updated in the same commit for user-facing behavior, tooling, project
  structure, or public API changes
- Version bumps also update `CHANGELOG.md`, and the topmost release heading
  matches `src/chat_downloader/metadata.py::__version__`

## CI

GitHub Actions is the only supported hosted CI platform. Preserve push
coverage for all branches, pull-request coverage targeting `master`,
`workflow_dispatch`, Python matrix `["3.12", "3.13", "3.14"]`, `uv sync
--locked`, canonical `make ci`, read-only `contents` permission, concurrency
cancellation, and job timeout. Do not add Gitea, Forgejo, Codeberg, or
Woodpecker CI configuration.

## Commits

Subject format: `[topic] short imperative summary`. Topic is one of `build`,
`chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`,
or `test`. Aim for about 50 characters; 72 max; no trailing period. Bodies use
`- ` bullets, one per logical change, wrapped at 72 columns.

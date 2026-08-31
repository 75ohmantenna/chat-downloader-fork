# Repository Guide

## Project facts

`chat-downloader-fork` is a Python 3.12+ CLI and typed API for YouTube, Twitch,
and Kick live chat and replay. CI tests Python 3.12, 3.13, and 3.14. It is a
personal fork of `xenova/chat-downloader`; upstream does not support fork-owned
changes.

Important locations:

- `src/chat_downloader/chat_downloader.py`: thin public facade.
- `src/chat_downloader/models/`: canonical init, request, and run parameters.
- `src/chat_downloader/runtime/`: orchestration, iteration, deadlines, and
  shutdown.
- `src/chat_downloader/sites/`: provider implementations; provider directories
  have additional scoped `AGENTS.md` files.
- `src/chat_downloader/output/` and `src/chat_downloader/formatting/`:
  provider-neutral output and rendering.
- `tests/fixtures/`: maintained provider payload fixtures.
- `docs/architecture.md`: module inventory and dependency guardrails.
- `docs/capability-inventory.md`: behavior-preservation checklist.
- `docs/development-workflow-guide.md`: full local and release workflow.
- `docs/maintenance-backlog.md` and `docs/maintenance-decisions.md`: open work
  and durable design decisions.

Supported output extensions are `.jsonl` and `.txt`. `uv.lock`, `dist/`,
coverage files, and package metadata directories are generated; do not hand-edit
or commit build output. Update `uv.lock` with `uv lock` only when dependencies
intentionally change.

## Architecture invariants

- Keep the facade thin; put runtime orchestration in `runtime/`.
- Add user-facing init, request, or runtime fields to the matching dataclass in
  `models/` first. CLI help comes from dataclass metadata, while parser
  registration remains explicit in `cli_args.py`.
- Keep provider behavior in its site package. YouTube, Twitch, and Kick are
  import-independent, and provider-neutral runtime/output/formatting code must
  not import a concrete site package. `lint-imports` enforces these boundaries.
- Use `utils/json_types` accessors for incoming provider JSON. Avoid `Any` for
  raw payloads; reserve `dict[str, Any]` for genuinely heterogeneous
  accumulators.
- Split by cohesive behavior, not execution phase or line count. A helper that
  requires its parent object and a broad protocol belongs on that class. Extract
  pure reusable helpers; accompany an extracted helper cluster with a real
  composition test.
- Do not create import-only re-export barrels or forwarding methods. The
  400-line ratchet is a smell signal, not a reason for temporal decomposition;
  allowlist an unusually large cohesive module with a rationale.

## Setup and commands

```bash
make setup
uv run pytest tests/FILE.py -q
uv run pytest tests/FILE.py::test_name -q
uv run pytest -q -p no:rerunfailures -m "not network"
uv run pytest -v -m network --run-network
make lint
make spell
make fmt-check
make typecheck
uv run lint-imports
make ci
```

`make setup` installs pre-commit/pre-push hooks and runs `uv sync`. Network tests
are opt-in. `make ci` is canonical validation: lock check, lint, spelling,
format check, type checking, 100% offline line coverage, build, and smoke test.

Minimum verification:

- Documentation-only: `make spell` plus relevant documentation contract tests.
- Source or test: focused tests while iterating, then lint, format, mypy,
  `lint-imports`, and the offline suite.
- Parser, retry, output, runtime, or public API: add a regression test; promote a
  curated fixture before changing parser behavior.
- Tooling, packaging, architecture, or cross-cutting changes: run `make ci`.

## Code and documentation conventions

- Source files start with `from __future__ import annotations`; Ruff owns the
  88-column, double-quoted format. Put type-only imports under `TYPE_CHECKING`.
- Mccabe complexity is capped at 10. Use `# noqa: C901` only for an intrinsically
  branchy function and include a short rationale.
- Name tests `test_<behavior>.py` or `test_<area>_unit.py`. Coverage pragmas are
  only for defensive or unreachable branches and require a one-line reason.
- Use `--logging debug` or `--verbose` for parser/transport investigation.
  `--testing` also pauses on debug conditions. Reserve `debug_log()` for
  unexpected data-quality conditions.
- Update the owning guide with user-facing behavior, public API, project
  structure, or tooling changes. Version changes must update `CHANGELOG.md`;
  its top release heading must match `metadata.py::__version__`.
- Keep architecture package inventories aligned with source. Preserve local
  links, CLI defaults/groups, public imports, facade/request parameter parity,
  Makefile contracts, `Any` density, and module-size ratchets.
- `.git-blame-ignore-revs` records the repository-wide formatting commit; set
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` when using blame.

## Project policies

- Check `git status --short --branch` before editing. Work on the requested
  branch and preserve unrelated changes. Do not weaken ratchets or reopen a
  closed maintenance deferral without documented evidence.
- Do not file fork-originating issues or pull requests upstream. Do not cite or
  link upstream issue/PR discussions in project docs, source, tests, fixtures,
  or release notes; keep regression context self-contained.
- GitHub Actions is the only hosted CI. Preserve pushes for all branches, pull
  requests targeting `master`, manual dispatch, Python 3.12–3.14, locked sync,
  `make ci`, read-only contents permission, concurrency cancellation, and the
  job timeout. Do not add other hosted CI configurations.
- Commit subjects use `topic: imperative summary`, where topic is `build`,
  `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, or
  `test`. Keep subjects at 72 characters or fewer with no trailing period.
  Bodies use one `- ` bullet per logical change, wrapped at 72 columns.
- Never put a bare issue reference such as `#123` in a commit message. The
  history check covers merge commits too. Merge pull requests with an explicit
  subject, for example:

  ```bash
  gh pr merge <N> --merge --subject "topic: short imperative summary"
  ```

  Prefer a merge commit when the branch contains a release tag so the tagged
  commit remains reachable from `master`. Reverting a violating merge does not
  remove it from reachable history; amend and force-push before others build on
  it.

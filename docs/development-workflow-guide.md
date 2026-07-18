# Development Workflow Guide

Canonical local-development reference for `chat-downloader-fork`. For module
ownership and dependency rules, use [`architecture.md`](architecture.md). For
user-facing commands, use [`cli-usage.md`](cli-usage.md).

This is a personal fork of `xenova/chat-downloader`; it does not accept support
requests or contributions. See the [README](../README.md) for the complete
support and AI-assistance disclosures.

## Tooling baseline

- Python support: 3.12+; CI validates 3.12, 3.13, and 3.14.
- Dependency and build metadata: `pyproject.toml` and `uv.lock`.
- Tests and coverage: pytest and coverage.py.
- Formatting and linting: Ruff, with an 88-character line length.
- Type checking: mypy, configured in `mypy.ini`.
- Import boundaries: import-linter, configured in `pyproject.toml`.
- CLI/API parameter definitions: `src/chat_downloader/models/`.

The source-module inventory and layer contracts live only in
[`architecture.md`](architecture.md); do not duplicate that inventory here.

## Setup

Install dependencies and Git hooks:

```bash
make setup
```

The equivalent explicit commands are:

```bash
uv sync
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
```

The pre-commit stage runs Ruff lint and format checks. The pre-push stage runs
mypy. Hooks use `uv run --locked` so they match the committed lockfile.

## Daily workflow

1. Check the branch and preserve unrelated work.
2. Make the smallest cohesive code or documentation change.
3. Run the narrowest relevant offline test while iterating.
4. Run the standard validation appropriate to the changed surface.
5. Update the document that owns any changed behavior or public contract.

Network tests are opt-in. New live-network tests require
`@pytest.mark.network`; parser and transport regressions should normally use
curated fixtures under `tests/fixtures/`.

## Commands

Focused tests:

```bash
uv run pytest tests/FILE.py -q
uv run pytest tests/FILE.py::test_name -q
```

Offline suite:

```bash
uv run pytest -q -p no:rerunfailures -m "not network"
```

Static checks:

```bash
uv run ruff check src/chat_downloader tests
uv run ruff format --check src/chat_downloader tests
uv run mypy .
uv run lint-imports
```

Opt-in network tests:

```bash
uv run pytest -v -m network --run-network
```

### Make targets

| Target | Purpose |
| --- | --- |
| `make setup` | Install Git hooks and synchronize dependencies |
| `make lock` | Update `uv.lock` |
| `make lock-check` | Verify that `uv.lock` matches project metadata |
| `make test` | Run the offline test suite |
| `make lint` | Run Ruff and import-linter |
| `make fmt` / `make fmt-check` | Apply or check Ruff formatting |
| `make typecheck` | Run mypy |
| `make coverage` | Run the offline suite with 100% line coverage enforced |
| `make build` | Build the wheel and source distribution |
| `make smoke` | Build and install the wheel in an isolated environment |
| `make check` | Run the fast local lint, format, type, and test path |
| `make ci` | Run the complete canonical validation path |

`make ci` runs `lock-check`, `lint`, `fmt-check`, `typecheck`, `coverage`, and
`smoke`. GitHub Actions invokes this exact target after `uv sync --locked`.

## Architecture and test guardrails

[`AGENTS.md`](../AGENTS.md) defines the binding rules;
[`architecture.md`](architecture.md) lists the enforced layer, API, size,
complexity, typing, coverage, and inventory checks. Treat them as change
detectors, not substitutes for design judgment. Rationale and reopen criteria
for non-obvious choices live in
[`maintenance-decisions.md`](maintenance-decisions.md).

## Documentation ownership

Update one authoritative document instead of copying the same explanation into
several places.

| Document | Owns |
| --- | --- |
| `README.md` | Project identity, installation, first run, support policy |
| `docs/cli-usage.md` | CLI recipes, flags, output behavior, user troubleshooting |
| `docs/python-api-reference.md` | Public Python objects, configuration, exports |
| Site integration guide | Provider flow, fragility points, drift workflow |
| `docs/architecture.md` | Module ownership and dependency rules |
| `docs/capability-inventory.md` | Behavior-preservation checklist |
| `docs/maintenance-backlog.md` | Open maintainability work only |
| `docs/maintenance-decisions.md` | Current design rationale and reopen criteria |
| `CHANGELOG.md` | User-observable and release-relevant changes |
| `AGENTS.md` / nested `AGENTS.md` | Binding agent workflow constraints |

The changelog is not a commit diary. Internal refactors, test churn, and doc
maintenance belong in Git history unless they materially change behavior,
compatibility, packaging, validation, or contributor workflow.

When the public import surface changes, keep package `__all__` declarations,
`docs/python-api-reference.md`, and public-API tests aligned.

## Debug sample capture

Unexpected provider payloads can be captured as sanitized JSON while debug
logging is enabled:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" --logging debug
```

Snapshots default to a temporary directory. Set
`CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR` to choose a stable location. Review captures
before promoting them into `tests/fixtures/`; captured data is evidence, not an
automatically trusted fixture.

Provider-specific diagnosis and fixture-promotion steps live in the
[YouTube](youtube-integration-guide.md),
[Twitch](twitch-integration-guide.md), and
[Kick](kick-integration-guide.md) integration guides.

## Version bumps

1. Update `src/chat_downloader/metadata.py::__version__`.
2. Add the new version as the topmost numbered `CHANGELOG.md` release heading.
3. Leave `pyproject.toml` unchanged; setuptools reads the version dynamically.
4. Run `uv lock`, `uv lock --check`, `uv sync --locked`, and `make ci`.
5. Inspect the built wheel metadata and confirm the expected version.

`tests/test_release_metadata_unit.py` enforces agreement between the package
version and the topmost numbered changelog release.

## Hosted CI

GitHub Actions is the only supported hosted CI platform. The workflow validates
Python 3.12, 3.13, and 3.14 on pushes to every branch, pull requests targeting
`master`, and manual dispatch. It uses locked dependencies, read-only contents
permission, concurrency cancellation, and a job timeout.

Do not add Gitea, Forgejo, Codeberg, or Woodpecker CI configuration.

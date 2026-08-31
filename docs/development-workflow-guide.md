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
- Spelling: codespell over every Git-tracked file and filename.
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
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
uv sync
```

The pre-commit stage runs Ruff lint and format checks plus codespell over every
tracked file. The pre-push stage runs mypy and rejects fork-history commit
messages that could notify an upstream issue tracker. Avoid bare issue-number
syntax in commit subjects and bodies; describe the local change without an
issue reference instead. Hooks use `uv run --locked` so they match the
committed lockfile.

## Daily workflow

1. Check the branch and preserve unrelated work.
2. Make the smallest cohesive code or documentation change.
3. Run the narrowest relevant offline test while iterating.
4. Run the standard validation appropriate to the changed surface.
5. Update the document that owns any changed behavior or public contract.

Network tests remain opt-in locally. Every external check carries
`@pytest.mark.network`, one scope marker (`network_replay`, `network_live`, or
`network_environment`), and a hard timeout. Parser and transport regressions
should normally use curated fixtures under `tests/fixtures/`. GitHub Actions
runs the stable `network_replay` contracts weekly and on manual dispatch;
volatile live and environment checks do not gate pushes or pull requests.

## Commands

Focused tests:

```bash
uv run pytest tests/FILE.py -q
uv run pytest tests/FILE.py::test_name -q
```

### Capture parity audit

After collecting the same run as `.jsonl` and `.txt`, audit the artifacts
offline with the exact format name used by the production run:

```bash
uv run python scripts/audit_capture_parity.py \
  capture.jsonl capture.txt --format twitch
```

`--format` is required because an artifact does not record its resolved
provider format. Use the resolved name that produced the text, such as
`twitch`, `kick`, or `youtube_live_default`, rather than the unresolved
site-default marker. Pass `--format-file path/to/formats.json` when the capture
used custom formatting. If the capture used a nondefault formatted-message
dedup cache, pass the same nonnegative value with
`--max-seen-message-ids`; zero has the production default meaning.

By default, the JSONL input represents one logical retrieval run. For JSONL
and TXT files formed by appending separate runs, repeat
`--dedup-reset-before-jsonl-line LINE` in strictly increasing order at each
later run's first one-based JSONL line. A reset beyond the input is an audit
failure.

The auditor parses every JSONL line as an object, applies the same bounded
paid/ticker semantic deduplication as formatted production outputs, renders
with `ItemFormatter`, and compares exact physical TXT content. Nonempty JSONL
and TXT files must end in LF or CRLF, must use one newline style consistently,
and must agree on the capture-origin style, so a Windows capture audits
correctly on a POSIX host and vice versa. Memory scales with the largest
individual input record plus the bounded dedup cache and explicitly supplied
reset list, rather than with total capture size.

Reports contain only counts, completeness state, and first issue/mismatch
indices; captured chat and rejected argument values are never printed. A JSON
parse, deduplication, or formatting error makes exact realignment unsafe, so
`comparison_complete=no` and `comparison_skipped` report how many JSONL lines
were not compared from that point onward. The tool still scans every JSONL and
TXT physical line for bounded diagnostics.

Only a truly absent artifact is treated as empty, matching lazy writers that
retrieve no records. Permission failures, dangling links, nonregular files,
and two input paths that open the same device/inode are usage/I/O errors. A
missing artifact still fails parity when its counterpart contains records.

Exit status `0` means parity passed, `1` means the artifacts failed audit, and
`2` means command usage, format configuration, or input I/O failed. Invalid
UTF-8, malformed or blank JSONL lines, non-object JSON, formatting failures,
line-count differences, mixed/missing/mismatched physical newlines, and invalid
dedup reset boundaries are audit failures.

Offline suite:

```bash
uv run pytest -q -p no:rerunfailures -m "not network"
```

Static checks:

```bash
uv run ruff check src/chat_downloader tests
uv run ruff format --check src/chat_downloader tests
git ls-files -z | xargs -0 uv run codespell
uv run mypy .
uv run lint-imports
```

Documentation and release contracts:

```bash
uv run pytest -q \
  tests/test_documentation_contract_unit.py \
  tests/test_architecture_doc_contract_unit.py \
  tests/test_release_metadata_unit.py
```

Opt-in network tests:

```bash
# Stable replay and immutable-resource contracts
uv run pytest -v -m network_replay --run-network

# Volatile live-channel and WebSocket smoke checks
uv run pytest -v -m network_live --run-network

# Proxy, header, and cookie integration checks
uv run pytest -v -m network_environment --run-network

# Every external check
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
| `make spell` | Run codespell over every tracked file and filename |
| `make fmt` / `make fmt-check` | Apply or check Ruff formatting |
| `make typecheck` | Run mypy |
| `make coverage` | Run the offline suite with 100% line coverage enforced |
| `make build` | Build the wheel and source distribution |
| `make smoke` | Build and install the wheel in an isolated environment |
| `make check` | Run the fast local lint, format, type, and test path |
| `make ci` | Run the complete canonical validation path |
| `make clean` | Remove caches, coverage data, build output, and package metadata |

`make ci` runs `lock-check`, `lint`, `spell`, `fmt-check`, `typecheck`,
`coverage`, and `smoke`. GitHub Actions invokes this exact target after
`uv sync --locked`.

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

Source code, dataclass metadata, constants, and tests are authoritative when
prose and implementation disagree. Documentation contract tests keep module
inventories, typed field/default tables, CLI flags, output formats, public
exports, and provider message-group tables aligned with those sources.

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

For clean-run diagnosis, YouTube can also capture the first three structurally
valid continuation responses from each retrieval run. This requires a separate
explicit opt-in because successful responses contain ordinary public chat data:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES=1 \
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" --logging debug
```

The per-run attempt limit avoids repeatedly sanitizing large responses after
the cap. The shared per-label limit also prevents more than three unique
successful-response files in one process and output directory. API-error and
structurally incomplete response bodies are not classified or captured as
successful responses.

Callers with several related anomaly labels can also provide `sample_group`
and `group_limit` to enforce a second aggregate bound. Kick uses this for
unsupported Pusher event names: each event name can retain up to three unique
samples while the entire unknown-event group remains capped at ten. A noisy
event therefore cannot consume another event name's individual quota.

Snapshots default to a temporary directory. Set
`CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR` to choose a stable location. Review captures
before promoting them into `tests/fixtures/`; captured data is evidence, not an
automatically trusted fixture.

On supported POSIX systems, the capture directory must be owned by the current
user with mode `0700`; sample files use mode `0600`. Capture rejects symbolic
links, unexpected file types, foreign ownership, and broader permissions rather
than risk exposing or redirecting diagnostic data. If secure directory-relative
no-follow creation is unavailable, capture fails closed and logs a warning
instead of writing through a path-based fallback.

Capture sanitization recursively removes known cookie, proxy, token, and API
key fields. It also treats custom header names containing authentication,
credential, secret, or token markers—and values using common authentication
schemes—as sensitive. Review remains mandatory because provider payloads can
introduce new secret-bearing shapes. Successful YouTube samples also retain
public chat contents after credential sanitization, so review them before
sharing.

Twitch supports clean-run IRC inspection with
`CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1`. Combined with the shared capture
flag and debug logging, it captures at most the first three raw IRC frames that
successfully parse. The per-run attempt cap spans reconnects and preserves the
original `\r\n` terminator so reviewed samples can be promoted directly into
live-event fixtures.

For event-diverse Twitch inspection, use
`CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES=1`. It captures the first
successfully parsed raw frame for each known normalized message type, or for a
bounded, collision-resistant raw-action fallback when the parsed type is
unknown. A message key requires a recognized raw `msg-id`; normalized-looking
unknown values use the action fallback. Without `msg-id`, only known raw actions
can use the parsed normalized type. Unknown raw actions are sanitized before
hashing and use opaque labels, preventing provider or credential fragments from
entering filenames and capture logs. The fixed per-run limit is 12 attempted
event keys across reconnects, with one successful sample per key and one retry
after a failed write. The backend separately caps this sample group and each
label per process and output directory; later runs may retain fewer than 12. An
exact prior payload can reuse its path, but a different payload for that label
is rejected even with free group slots. Labels and in-memory keys are path-safe
and length-bounded; payloads use the same sanitization and retain `\r\n`.
Enabling this together with the first-three mode is additive: no more than 15
clean-traffic raw-frame samples are captured, and one source frame can appear
in both modes. Both modes run before live deduplication and output filtering.
Separate drift samples can add files, and unknown frames can overlap the event
mode's fallback capture.

Kick supports the equivalent clean-run workflow with
`CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1`. Combined with the shared capture flag
and debug logging, it captures at most the first three raw WebSocket frames per
normalized event type that successfully parses. Type-specific per-run attempt
caps span reconnects and exclude control, unknown, and malformed frames.

The logging handler applies the same structured and string redaction to project
messages, exception text, and stack information. It redacts credentials in
URLs and sensitive query or labeled values, including continuation tokens and
credential-shaped Google API keys in urllib3 request-target messages. Ordinary
non-secret `key` query values remain visible. The handler then renders control
characters visibly to prevent forged terminal output.

Provider-specific diagnosis and fixture-promotion steps live in the
[YouTube](youtube-integration-guide.md),
[Twitch](twitch-integration-guide.md), and
[Kick](kick-integration-guide.md) integration guides.

## Release procedure

1. Confirm `master` is clean and synchronized with `origin/master`.
2. Select the next semantic version from the user-visible changes since the
   latest `v*` tag.
3. Move relevant `Unreleased` entries under a new numbered release heading,
   update `src/chat_downloader/metadata.py::__version__`, and keep that version
   as the topmost numbered `CHANGELOG.md` heading. Leave `pyproject.toml`
   unchanged; setuptools reads the version dynamically.
4. Run `uv lock`, `uv lock --check`, `uv sync --locked`, and `make ci`.
5. Inspect the built wheel metadata and confirm the expected version.
6. Create and verify the signed release commit, then create and verify the
   signed annotated `v<version>` tag at that commit.
7. If the release was prepared in a second checkout, fetch its branch into the
   target checkout, prove the target `master` is an ancestor, and merge with
   `--ff-only`. A fast-forward preserves the reviewed commit identities and is
   preferable to replaying a linearly related branch through cherry-picks.
8. Push `master` and the new tag, then confirm both remote refs resolve to the
   expected commits.

`tests/test_release_metadata_unit.py` enforces agreement between the package
version and the topmost numbered changelog release.

## Hosted CI

GitHub Actions is the only supported hosted CI platform. The workflow validates
Python 3.12, 3.13, and 3.14 on pushes to every branch, pull requests targeting
`master`, and manual dispatch. It uses locked dependencies, read-only contents
permission, concurrency cancellation, and a job timeout. The checkout retains
full Git history so the fork-history issue-reference guard can inspect every
commit after its recorded baseline.

Do not add Gitea, Forgejo, Codeberg, or Woodpecker CI configuration.

# Development Workflow Guide

Canonical local-development reference for `chat-downloader-fork`. For module
ownership and dependency rules, use [`architecture.md`](architecture.md). For
user-facing commands, use [`cli-usage.md`](cli-usage.md).

This is a personal fork of `xenova/chat-downloader`; it does not accept support
requests or contributions. See the [README](../README.md) for the complete
support and AI-assistance disclosures.

## Tooling baseline

- Python support: 3.12+; the CI matrix targets 3.12, 3.13, and 3.14.
- Dependency and build metadata: `pyproject.toml` and `uv.lock`.
- Tests and coverage: pytest and coverage.py.
- Formatting and linting: Ruff, with an 88-character line length.
- Ruff checks `src/chat_downloader`, `tests`, and `scripts` in local hooks and
  Makefile commands.
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
should normally use curated fixtures under `tests/fixtures/`. The GitHub Actions
workflow schedules stable `network_replay` contracts weekly and on manual
dispatch; volatile live and environment checks do not gate pushes or pull
requests.

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

YouTube live default and `24_hour` output show UTC `HH:MM:SS` clock time,
including seconds. To audit older files with the previous date-inclusive or
minute-only live formats, supply a custom format file matching the original
capture. Newly captured files use the current built-in definitions.

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

### YouTube capture inspection

YouTube captures using `--verify_output` run this inspector after outputs close.
The run result, debug summary, and manifest retain the content-free report in
`provider_inspection`. Findings or inspection errors fail the verified run even
when output parity passes. Missing mobile timestamps remain observations.
Resumed files include checkpoint-verified prior records; known prior parser
loss remains a finding. Empty lazy captures are inspected without creating files.

Inspect a closed YouTube JSONL capture without printing messages or identifiers:

```bash
uv run python scripts/inspect_youtube_capture.py capture.jsonl \
  --manifest capture.manifest.json
```

The optional manifest adds profile/view provenance, bootstrap and continuation
connection counters, parser-loss counts, and record/type-count reconciliation.
Use the matching manifest from one retrieval run. A resumed file can contain
prior records: total counts include `prior_message_count`, while current-run
type counts are explicitly marked incomparable with the whole file.

The fixed report recognizes supported tooltip and banner-removal controls even
though they have no selectable message group. It includes unknown message types,
duplicate text-message IDs, invalid source timestamps or replay offsets, and
strict JSONL findings. Missing
source timestamps, zero-offset records without timestamps, signed preroll, and
offset backsteps are observations rather than automatic failures. Paid events
and their tickers may share IDs. Manifest input is limited to one MiB and must
contain an object with unique keys. Exit codes are `0` for clean inspection, `1`
for review, and `2` for usage or input errors. This offline inspector supplements
output parity; it does not establish provider delivery completeness.

### Twitch capture inspection

Live Twitch captures using `--verify_output` automatically include these
diagnostics in `provider_inspection` on the run result, debug summary, and run
manifest. To inspect a previously closed single live retrieval run offline:

```bash
uv run python scripts/inspect_twitch_capture.py capture.jsonl \
  --debug-log debug.log
```

The JSON report contains fixed diagnostic names, counts, and first issue line
numbers. It checks unknown message types, duplicate message IDs, missing text
identity/timestamp fields, malformed or ambiguous JSONL (including duplicate
keys, non-finite numbers, and lone Unicode surrogates), and every emote
location against its parsed name. It also reports badge/emote/reply counts and timestamp
backsteps without treating arrival-order timestamp reversals as errors.
Absent timestamps on control events such as room state are counted normally.
This supplements exact TXT parity; it does not prove the provider's original
payload was parsed correctly or that Twitch delivered every message.

`--debug-log` is optional. When supplied, it requires exactly one standard
`[DEBUG] Run summary:` line from the same run, with or without the current
UTC timestamp prefix, and reports received IRC frames
minus benign controls minus parsed messages. A nonzero gap warrants review;
unparsed frames at the retrieval deadline can explain a positive gap. The
report does not equate this gap with lost output messages, and cannot verify
that a supplied log belongs to the supplied capture. Appended multi-run logs
are rejected. Summary lines are limited to 64 KiB.

Exit `0` means no findings requiring review, `1` means findings or a frame
accounting gap, and `2` means input, summary, or temporary-storage failure.
An empty existing JSONL file reports zero records; missing or nonregular
input files are I/O errors. Chat contents, IDs, unknown type names, and rejected input values are
never printed. Exact duplicate detection uses a temporary SQLite database,
with disk usage proportional to unique IDs; other state is bounded apart
from the largest input line. Use this on one completed run, with closed files.
The existing parity auditor remains responsible for physical newline checks.

### Kick capture inspection

Kick live captures with `--verify_output` automatically run the same inspector
after outputs close, using in-memory diagnostics and run counts. No debug log
is required. The report is retained in `provider_inspection` on the run result,
debug summary, and run manifest. Review findings or inspector errors fail the
run independently of TXT parity. Closed partial captures retain findings
without replacing their original retrieval error. Empty lazy captures are
inspected without creating output files.

Interrupted captures also receive automatic TXT parity checks when cleanup
succeeds. A passing parity result certifies the captured prefix's formatting,
while the run remains interrupted and unsuccessful. The inspector can still
require review for an interrupted run or incomplete deadline accounting.
The additive `review_reasons` list explains that status with fixed codes:
`run_interrupted`, `run_failed`, `deadline_accounting_incomplete`,
`record_issues`, `accounting_gaps`, `provider_anomalies`, and
`replay_incomplete_or_inconsistent`. An interrupted run can have valid captured
records and passing TXT parity; its interruption alone does not establish
corruption. Record issues, provider anomalies, and accounting gaps remain
reported alongside interruption. Status and exit-code rules are unchanged.

To inspect an existing live capture or replay archive offline:

```bash
uv run python scripts/inspect_kick_capture.py capture.jsonl --debug-log debug.log
```

For live inspection, use one completed run, preferably captured with
`--message_groups all`, and its debug log. The log is optional. The content-free
JSON report checks
record IDs, text author IDs and timestamps, unknown types, invalid JSON,
duplicates, and named/unnamed emote spans in messages and replies. Timestamp
backsteps and absent provider timestamps on system events are informational.
Receive timestamps, when present, must be nonnegative integers.

With a log, it reconciles decoded frames against control, parsed, unsupported,
malformed, and Pusher-error counts. Unknown chat subtypes overlap parsed events
and are not subtracted twice. It compares preloaded/startup/live/reconnect
source totals,
the run message count, and per-type counts with JSONL. Recorded parser drops,
unknown types, invalid frames, incomplete history recovery, failed runs, and
inconsistent totals require
review even when frame accounting balances and TXT parity passes. Truncated
backfill windows and their uncovered time require review; reconnects and fully
recovered backfill alone are informational. Older logs lacking the truncation
counters default them to zero and cannot establish that no historical gap
occurred. Filters, deduplication, and deadline prefetch can make parsed counts
differ from output; a count gap is
evidence to investigate, not proof of message loss.

For an opt-in live CLI smoke matrix, run:

```bash
uv run python scripts/smoke_kick_live.py --run-network --channel xqc \
  --duration 480 --artifacts /tmp/kick-smoke-new
```

The artifact directory must not exist. The runner creates it with mode `0700`,
runs an eight-minute all-groups capture by default, then checks help/version,
quiet and compact output, text filtering, custom headers, inactivity shutdown,
and rejection of unsupported output extensions and live replay-only options.
Each capture writes JSONL, TXT, and a manifest with output verification. Child
processes have independent wall-clock limits and cannot wait for stdin. Short
cases default to ten seconds; `--case-duration` changes that limit. Very short
durations can expire during connection setup and correctly require review for
incomplete deadline accounting. Live availability and event delivery can also
cause the smoke run to fail.

Repeat `--proxy socks5://host:port` to add bounded all-groups proxy captures.
Proxy arguments apply only to those extra cases; other cases use the normal
environment proxy policy. The runner does not read a proxy config or persist
proxy arguments in its aggregate report. Shell arguments and history can still
contain credentials. Raw child logs and chat captures stay in the private
directory. Stdout and `report.json` contain only case names, exit codes, fixed
statuses, and counts. Exit zero means every case passed; exit one means at
least one case failed or required review. A nonempty capture is required except
for the inactivity case. This is observational live coverage; it cannot prove
server-side completeness or exercise every rare event.

`tests/test_kick_connection_drop_unit.py` supplements live observation with
real loopback WebSocket connections. Abrupt TCP closure and a WebSocket close
frame both drive reconnect, history backfill, duplicate suppression, verified
writers, and manifest finalization without external network access.

For a Kick VOD or clip replay, the same command detects reverse-history
diagnostics from the run summary and reconciles replay pages, records, skips,
filters, duplicates, emissions, and output counts. For appended replay captures,
repeat `--debug-log` once per run in append order. Each log must contain exactly
one bounded (64 KiB) standard run summary. Multiple logs are rejected for live
captures. Missing replay runs or incomplete final traversal require review;
counts cannot authenticate that logs belong to a capture.

Exit `0` means no review findings, `1` means review required, and `2` means an
input, summary, or temporary-storage error.
Reports never echo chat content, identifiers, unknown type names, or rejected
arguments. Exact duplicate tracking uses temporary SQLite storage. Memory
scales with the largest JSONL record, not total capture length. Missing or
nonregular inputs fail; an empty existing capture is valid. Run the separate
parity auditor to check exact TXT rendering and physical newlines.

Offline suite:

```bash
uv run pytest -q -p no:rerunfailures -m "not network"
```

Static checks:

```bash
uv run --locked ruff check src/chat_downloader tests scripts
uv run --locked ruff format --check src/chat_downloader tests scripts
git ls-files -z | xargs -0 uv run --locked codespell
uv run --locked mypy .
uv run --locked lint-imports
```

Strict type checking excludes ignored local `testing/` scratch scripts, along
with build artifacts and tests outside the maintained fixture/drift harnesses.

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
| `make check` | Run local lint, spelling, format, type, and offline test checks |
| `make ci` | Run the complete canonical validation path |
| `make clean` | Remove caches, coverage data, build output, and package metadata |

`make ci` runs `lock-check`, `lint`, `spell`, `fmt-check`, `typecheck`,
`coverage`, and `smoke`. GitHub Actions invokes this exact target after
`uv sync --locked`. CI pins uv to `0.13.6` and `astral-sh/setup-uv` to
`v10.3.3` by full commit SHA. Checkout `v7.0.1` and setup-python `v7.1.0` are
also pinned by full commit SHA. Python setup checks for the latest patch in
each supported minor series. Installation and validation pass the selected
interpreter path through `UV_PYTHON`, so the local `.python-version` cannot
override the CI matrix or the scheduled job's Python version. Use the same uv
version locally when validating tooling changes; standalone installations can
run `uv self update 0.13.6`.

## Dependency refreshes

Run `uv lock --upgrade` to refresh dependencies within the version constraints
in `pyproject.toml`, then `uv sync --locked` and `make ci`. Review the resolved
versions and release notes, especially for networking libraries. Commit the
generated `uv.lock` when intentionally updating dependencies. Wheel installers
use the requirements in `pyproject.toml`, not this lockfile. Raise minimum
versions there when a fix must reach installed users. The current security
floors are requests 2.34.2, certifi 2026.10.23, urllib3 2.8.0, curl-cffi
0.17.0, and setuptools 84.2.1 for isolated builds. The urllib3 floor covers
HTTPS proxy TLS and streamed response security fixes. The certifi floor
removes trust in a certificate authority affected by compromised intermediate
keys. Curl-cffi 0.17.0 is the stable release that bundles patched Brotli and
libcurl. The offline Kick transport checks inspect the loaded native libraries
and exercise identity, gzip, and Brotli response decoding.

Use refreshed managed Python builds: the October 2026 baseline is Python
3.12.16, 3.13.18, and 3.14.10. These security releases block compromised
certificate authority keys in Python's `ssl` module. After updating uv, install
the refreshed builds with
`uv python install --reinstall 3.12.16 3.13.18 3.14.10`, then recreate the
project environment with `uv venv --python 3.12.16 --clear` and
`uv sync --locked`. Existing environments do not automatically switch to an
updated interpreter. The `.python-version` file selects the 3.12 series;
installed environments still need to be recreated after a patch update.
Upgrade pip to at least 26.2.2 wherever it is installed; updating the project's
certifi alone does not replace pip's bundled trust store. It also does not
refresh operating-system or custom CA bundles:
curl-cffi honors CA environment overrides and can use Python's default trust
store. Refresh those stores separately when they are configured. The locked
virtualenv also bundles patched pip for supported Python versions. Keep TLS
verification enabled and verify release checksums when bootstrapping tools
from an environment with an older trust store.

## Architecture and test guardrails

[`AGENTS.md`](../AGENTS.md) defines the binding rules;
[`architecture.md`](architecture.md) lists the enforced layer, API, size,
complexity, typing, coverage, and inventory checks. Treat them as change
detectors, not substitutes for design judgment. Rationale and reopen criteria
for non-obvious choices live in
[`maintenance-decisions.md`](maintenance-decisions.md).

Keep detailed diagnostic CLI scenario matrices in-process, restoring logger
state and asserting return codes, stdout, stderr, and content-free errors.
Retain subprocess representatives for script entry points, process exit codes,
and nonblocking rejection of special files. Test streaming by checking that
processing begins before the entire input has been consumed; record counts
alone do not establish bounded-memory behavior.

Use controlled clocks for polling assertions and events to coordinate blocked
workers. Keep real timer composition checks, but avoid narrow elapsed-time
limits that depend on scheduler speed. Before removing a test, identify the
remaining assertions that preserve its behavior and failure modes.

## Documentation ownership

The actual checked-in codebase is the single source of truth for all
documentation of implemented behavior, public interfaces, defaults, project
structure, and executable tooling. Derive claims from source code, bundled
data, and configuration. Give each explanation one owning document and link to
it elsewhere; ownership identifies where prose is maintained, not a second
source of truth.

This rule also applies to code comments, docstrings, CLI help, examples, and
configuration comments. Describe units, mutation, side effects, retry bounds,
and lifecycle ownership from the actual implementation; do not retain claims
from an earlier architecture merely because a test still passes.

Before adding or changing a claim, inspect its implementation using the source
map below. When prose and implementation disagree, correct the prose. Change
code only for a separately established bug or requested behavior change, with
the relevant regression test. Tests verify behavior and protect intended
contracts; fixtures and upstream documentation provide evidence to investigate,
not definitions of this fork's behavior. Historical changelog entries describe
the code at their release; backlog proposals must be clearly identified as
unimplemented. Workflow policies and design rationale record decisions rather
than establish runtime behavior.

| Claim | Codebase source to inspect |
| --- | --- |
| Public API and typed fields/defaults | `src/chat_downloader/__init__.py`, `src/chat_downloader/chat_downloader.py`, `src/chat_downloader/models/`, `src/chat_downloader/runtime/` |
| CLI flags, help, and argument handling | `src/chat_downloader/cli.py`, `src/chat_downloader/cli_args.py`, `src/chat_downloader/models/` |
| Provider URLs, message groups, parsing, and transport | The matching package under `src/chat_downloader/sites/` |
| Output extensions, writing, and text rendering | `src/chat_downloader/output/`, `src/chat_downloader/formatting/`, `src/chat_downloader/sites/output_dispatch.py` |
| Optional stdout compaction and provider state eligibility | `src/chat_downloader/models/_runconfig.py`, `src/chat_downloader/runtime/runner.py`, `src/chat_downloader/sites/_message_dedup.py`, `src/chat_downloader/sites/base.py`, and the matching provider implementation |
| Module layout and import boundaries | The source tree and `[tool.importlinter]` in `pyproject.toml` |
| Package metadata, Python requirements, and dependencies | `pyproject.toml`, `src/chat_downloader/metadata.py`, `uv.lock` |
| Development commands, hooks, checks, and hosted CI | `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, and the relevant tool configuration and check implementation |

Documentation contract tests keep module
inventories, literal repository paths, typed field/default tables, CLI flags
and retrieval examples, request profiles, output formats, public exports, and
provider message-group and live-diagnostic tables aligned with those sources.
Run the documentation and release contracts listed under Commands, plus
`make spell`, after documentation changes. Passing these checks covers their
specific assertions; review narrative claims against code as well.

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
Samples are staged privately and published atomically without replacement only
after the complete file is synchronized. Publication requires same-filesystem
hard-link support. Reusing a deterministic filename validates the stored bytes
against the sanitized payload; incomplete or different contents are rejected,
not reported as a successful capture. Preserve a rejected sample for inspection
and remove it explicitly before retrying. An abrupt stop can leave a private
staging file, but not a partially published sample.
Failures before publication release quota claims for retry. A complete published
or verified existing sample retains its label and group quota slots even if
persistence confirmation fails; capture still warns and returns no successful
path.

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
The event mode also reserves three attempts each for emote-bearing text and
replies, under fixed `text-shape-emotes` and `text-shape-in-reply-to` labels.
These six attempts are independent of the event-key quota, span reconnects,
and run before filtering and deduplication. Failed writes consume an attempt.
Enabling this together with the first-three mode is additive: no more than
21 clean-traffic raw-frame samples are captured, and one source frame can
appear in multiple quotas. Both modes run before live deduplication and output
filtering.
Separate drift samples can add files, and unknown frames can overlap the event
mode's fallback capture.

Kick supports the equivalent clean-run workflow with
`CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1`. Combined with the shared capture flag
and debug logging, it captures at most the first three raw WebSocket frames per
normalized event type that successfully parses. Type-specific per-run attempt
caps span reconnects and exclude control, unknown, and malformed frames.

Replies, emote-bearing text, and badge-bearing text each have an additional
independent three-attempt quota under `text-shape-in-reply-to`,
`text-shape-emotes`, and `text-shape-badges` labels. Shape detection uses the
normalized reply, emote, and author-badge fields. These quotas add at most nine
samples per run; overlapping shapes can capture the same frame under multiple
labels. All quotas span reconnects, run before deduplication and message
filtering, and retain the shared capture opt-in, redaction, and private-file
requirements. Failed writes consume an attempt. Shared per-label limits can
reduce the number of new samples when reusing a directory in one process.

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

1. Confirm `master` is clean and synchronized with `origin/master`. Check local
   and remote release tags; a version in metadata or a changelog heading alone
   does not establish that it was released.
2. Select the next semantic version from the user-visible changes since the
   latest `v*` tag.
3. Move relevant `Unreleased` entries under a new numbered release heading,
   update `src/chat_downloader/metadata.py::__version__`, and keep that version
   as the topmost numbered `CHANGELOG.md` heading. Leave `pyproject.toml`
   unchanged; setuptools reads the version dynamically.
4. Run `uv lock`, `uv lock --check`, `uv sync --locked`, and `make ci`.
5. Inspect the built wheel metadata and confirm the expected version. Confirm
   validation for the release commit across Python 3.12–3.14. Workflow files
   alone do not prove execution: GitHub Actions must be enabled, and the runs
   must cover the intended commit. Record any local matrix or opt-in network
   validation separately from hosted results.
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

GitHub Actions is the only supported hosted CI platform. The workflow defines
a `test` job for `make ci` on Python 3.12, 3.13, and 3.14 on pushes to every
branch, pull requests targeting `master`, and manual dispatch. The separate
Python 3.14 `network-replay` job runs stable replay contracts on the weekly
schedule and manual dispatch. Both jobs use locked dependencies, read-only
contents permission, concurrency cancellation, and a 20-minute timeout. The `test`
checkout retains full Git history so the fork-history issue-reference guard can
inspect every commit after its recorded baseline. Repository-level Actions
settings control whether these jobs execute; check actual run results before
claiming hosted validation.

Do not add Gitea, Forgejo, Codeberg, or Woodpecker CI configuration.

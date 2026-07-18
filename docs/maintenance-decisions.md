# Maintenance Decisions

Current rationale for architectural choices that are not obvious from the code
alone. These are constraints with reopen criteria, not a chronological work
log. Git and `CHANGELOG.md` preserve implementation history; source and tests
are authoritative for metrics.

## Split by cohesion, not phase or size

**Decision:** A module boundary must concentrate a behavior or remove coupling.
Do not split setup, iteration, and response handling into separate phase modules
that a reader must mentally reassemble. Pure helpers are good extraction
candidates; helpers that accept an entire parent object usually belong on that
object.

The 400-line test is a smell detector. A cohesive module may be allowlisted with
an inline rationale instead of being fragmented to satisfy a metric.

**Revisit when:** a feature or defect demonstrates a new ownership boundary, or
a module accumulates a second independent responsibility.

## Keep provider implementations independent

**Decision:** YouTube, Twitch, and Kick parsing, badges, transports, retry
decisions, and payload normalization remain in their site packages. Similar
output shapes do not justify a shared parser when the source protocols and
failure modes differ.

Generic runtime, output, and formatting layers remain provider-neutral. Shared
behavior belongs there only when it has a provider-independent contract and
more than one real consumer.

**Revisit when:** a new provider or repeated cross-provider change shows that a
shared abstraction would remove knowledge rather than merely move code.

## Distinguish raw JSON from assembled output

**Decision:** Incoming platform payloads use `utils/json_types` aliases and
narrowing accessors such as `get_str`, `get_int`, `get_dict`, `get_list`, and
`dig`. Heterogeneous dictionaries assembled by parsers and formatters may retain
`dict[str, Any]` when a `TypedDict` would require a large optional-field schema
or fight incremental construction.

The per-module `Any` test is a non-regression floor. Lower a cap when related
typing work removes occurrences; never raise a cap merely to make a change
pass.

**Revisit when:** a stable output schema or a low-boilerplate typed accumulator
can replace an existing heterogeneous boundary.

## Enforce line coverage, inspect branch coverage

**Decision:** `make ci` enforces 100% deterministic offline line coverage.
Branch coverage is useful as an investigative report but is not a gate. The
remaining uncovered branches observed during the 2026 hardening pass were
defensive, platform-specific, or shutdown-race paths where forcing 100% would
reward artificial tests or broad exclusions.

Coverage pragmas are limited to defensive or unreachable branches and require
an inline reason.

**Revisit when:** meaningful branch gaps accumulate or the test architecture
can cover the remaining paths without implementation-shaped tests.

## Keep the YouTube continuation loop cohesive

**Decision:** `sites/youtube/continuation.py` owns the stateful live/replay
continuation behavior as one unit: context construction, request-profile
fallback, response handling, progress guards, and iteration. Pure timing,
filter, URL, and state helpers remain in `continuation_helpers.py`.

The loop is intentionally allowlisted above 400 lines. Its integration tests
must exercise the assembled path, not only isolated helper stages.

**Revisit when:** a genuinely independent behavior emerges that does not need
the loop's state or downloader/session ownership.

## Keep transport ownership explicit

**Decision:** Shared HTTP state is downloader-owned and closed through the
normal chat/downloader lifecycle. Twitch owns its IRC transport and reconnect
policy; Kick owns its HTTP client and Pusher transport; YouTube owns its HTTP
continuation retry and profile-fallback behavior. Transport-specific recovery
is not hidden behind a generic cross-site retry facade.

Early termination must propagate `close()` through runtime wrappers before
writers are finalized. Reconnect loops remain bounded and reset failure streaks
only after useful traffic.

**Revisit when:** a provider-independent lifecycle primitive can replace
duplicated mechanism without absorbing provider-specific policy.


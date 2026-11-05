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

## Keep checkpoint verification and persistence together

**Decision:** `runtime/capture_checkpoint.py` owns request and artifact identity,
event-kind and occurrence boundaries, accepted-record rollback, and atomic
checkpoint persistence. A reproduced inconsistent boundary (`offset: null`
with recorded events) allowed duplicate appends despite matching output hashes.
Validate the offset, event identities, occurrence counts, and total together
before binding a source. The module is narrowly allowlisted above 400 lines;
separating validation from this state would obscure the append contract.

**Revisit when:** checkpoint validation has an independent consumer or a new
ownership boundary removes coupling without splitting its integrity checks.

## Keep Kick clip fallback evidence together

**Decision:** `sites/kick/clip_service.py` owns web and mobile clip-contract
validation, cross-endpoint evidence reconciliation, source-VOD fallback policy,
and bounded replay assembly as one cohesive security boundary. It is
intentionally allowlisted above 400 lines rather than split into temporal
lookup and response phases whose ordering would hide when identity and bounds
must fail closed.

**Revisit when:** a pure metadata-validation component gains another real
consumer, or a new ownership boundary can remove state and fallback coupling.

## Keep Kick HTTP response classification together

**Decision:** `sites/kick/api_client.py` owns origin-scoped HTTP requests and
their resource-specific status, challenge, identity, and JSON classification.
The recorded subscriber-only VOD response demonstrates why HTTP 403 alone
cannot identify a challenge: a validated video denial may enable public clip
metadata fallback, while challenge documents must remain terminal. The client
is narrowly allowlisted above 400 lines to keep that precedence and its
diagnostics in one place.

**Revisit when:** response policy gains an independent consumer or a new
boundary removes coupling without separating status from resource identity.

## Keep Twitch replay identity and pagination together

**Decision:** `sites/twitch/replay_service.py` owns VOD/clip metadata, bounded
cursor pagination, edge validation, and creator identity used during replay
parsing and badge lookup. It is narrowly allowlisted above 400 lines rather
than split into request phases that would obscure cursor and creator-ID state.

**Revisit when:** creator identity or edge validation becomes a pure component
with another real consumer, or a new boundary removes replay-loop coupling.

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
fallback, response handling, progress guards, and iteration. Downloader-independent
timing, filter, URL, and state helpers remain in `continuation_helpers.py`.

The loop is intentionally allowlisted above 400 lines. Its integration tests
must exercise the assembled path, not only isolated helper stages.

**Revisit when:** a genuinely independent behavior emerges that does not need
the loop's state or downloader/session ownership.

## Keep transport ownership explicit

**Decision:** Shared HTTP state is downloader-owned and closed through the
normal chat/downloader lifecycle. Twitch owns its IRC transport and reconnect
policy; Kick owns its HTTP clients and negotiated Pusher/Centrifugo transports;
YouTube owns its HTTP continuation retry and profile-fallback behavior.
Transport-specific recovery is not hidden behind a generic cross-site retry
facade.

Kick live orchestration accepts a structural feed lifecycle rather than a
concrete Pusher parent. The public owner configures negotiated protocol
transports through construction, including each endpoint's proxy and origin;
legacy discovery remains explicit in standalone Pusher construction. Injected
transports use the same cancellation owner as production. Setup retains HTTP
client ownership until its reader starts successfully, and checks cancellation
after blocking negotiation and connection operations before advancing setup.

Early termination must propagate `close()` through runtime wrappers before
writers are finalized. Reconnect loops remain bounded and reset failure streaks
only after useful traffic.

Provider-specific credential refresh is bounded too. In particular, a rejected
Kick Pusher application key may force one fresh discovery and reconnect, but a
repeated rejection is terminal. This keeps recovery close to the protocol that
defines the signal and prevents infinite reconnect loops.

Kick's low-level WebSocket module also owns pre-payload frame/fragment
reservations and installation of the bounded dependency frame buffer.
It is narrowly allowlisted above 400 lines so the reservation hook, real socket
construction, and receive/close contract remain visible together. The supported
dependency floor is websocket-client 1.9.2, whose framing/timeout behavior is
covered by the transport composition regressions.

**Revisit when:** a provider-independent lifecycle primitive can replace
duplicated mechanism without absorbing provider-specific policy.

## Compare output destinations after expansion

**Decision:** Output identity is evaluated only after `{title}` and `{id}`
metadata placeholders are expanded and the path is canonicalized. The runtime
pipeline suppresses resolved duplicates while preserving generated-name
provenance. Generated destinations require exclusive creation even when aliased
with an explicit path; fixed explicit paths retain overwrite/append semantics.
The dispatcher checks the complete target set before destructive initialization.
Validated regular files must have exactly one link; symlinks, hard links, and
unverifiable identities fail closed. Repair, writes, and interrupted rollback
use validated descriptors, never pathname reopens.

Channel discovery marks its initial metadata as provisional. Distinct templates
remain attached until real video metadata is available; the dispatcher checks
the entire resolved set before opening lazy writers. Already resolved targets
are still deduplicated during setup so verification sees the effective pair.

**Revisit when:** output targets grow beyond local files or a writer backend
provides a stronger destination-identity contract.

## Keep capture parity alignment in one state machine

**Decision:** `output/capture_parity.py` owns the provider-neutral streaming
relationship among opened artifact identity, JSONL validity, physical newline
style, formatted-message deduplication, rendering, and TXT alignment. It is
narrowly allowlisted above 400 lines because splitting those coupled checks by
scan phase would make comparison completeness and first-mismatch positions
implicit. Privacy-safe argument parsing and summary rendering remain in the
separate `scripts/audit_capture_parity.py` command layer.

**Revisit when:** input reading or semantic comparison gains an independent
production consumer with a contract that removes shared alignment state.

## Share strict capture reading across provider inspectors

**Decision:** `utils/capture_reader.py` owns regular-file opening, physical
JSONL line traversal, duplicate-key rejection, recursive finite-number and
Unicode validation, and content-free findings. YouTube, Kick, and Twitch
inspectors share this contract; they retain provider record
validation, exact ID membership, and ledger accounting. Kick and Twitch also
share bounded, exactly-one debug-summary framing and duplicate-key-safe literal
decoding in the same input module. Literal summaries remain separate from strict
JSONL validation and provider-specific accounting.

The parity auditor retains its own artifact identity and JSONL policy. Sharing
its decoder would change that contract rather than remove duplicate knowledge.
Reader tests use real temporary files and cover continuation and resource
closure; each provider retains a composition test through its inspector.

**Revisit when:** another capture consumer needs this same strict contract.

## Share Kick capture inspection with its offline command

**Decision:** `sites/kick/capture_inspection.py` owns provider record integrity,
exact duplicate tracking, and live/replay counter reconciliation. The offline
command and automatic live verification use the same content-free report.
The module is intentionally allowlisted above 400 lines: moving the existing
offline inspector into the provider package preserves its coupled record and
ledger validation rather than splitting it into scan phases. The command layer
retains argument parsing and exit-code handling.

Generic runtime passes run counts, provider diagnostics, and deadline accounting
to an inspector attached by the owning provider. It does not import a concrete
provider. Kick replay remains offline because appended archives require the
ledger from every resumed run, not just the current run's counters.

**Revisit when:** an independent integrity check gains a second production
consumer, or replay checkpoints retain enough ledger history for automatic
inspection of an appended archive.

## Keep orchestration and session ownership deep

**Decision:** Runtime callers cross two orchestration interfaces:
`dispatch_chat` constructs a configured chat, while `configure_chat` applies
the provider-neutral pipeline. URL correction, site-default resolution,
wrapper ordering, formatting, and writer setup remain implementation details.

The run result owns one deadline-accounting observation after bounded shutdown.
Provider inspection, manifest writing, and debug logging consume that same
observation: a worker can remain active after close, so an incomplete count is
a lower bound rather than a value to resample independently in each report.
Checkpoint ownership computes reset positions for both parity verification and
checkpoint saving; the runner does not reconstruct append arithmetic.
Observation failures retain an incomplete lower bound, preserve an earlier
capture error, and allow safe checkpoint and manifest persistence.
Tests drive these interfaces instead of treating each stage as a separate
caller surface.

Successful binding and failed cleanup remain distinct eligibility facts within
the cohesive run lifecycle. A controller that only forwards those facts and
their ordering would not deepen the interface. Revisit this ownership if
another production caller or an eligibility defect demonstrates a useful seam.

Checkpointed record consumption owns emission and replay-position observation
as one operation. The output dispatcher restores file prefixes and its own
successful-write ledger after a torn record; checkpoints never copy or replace
that private ledger. The runner counts only accepted records and does not
coordinate the rollback recipe. Failed consumption ends the capture, so its
formatted deduplication cache is not reused after rollback.
Failed file recovery remains sticky even when accepted-record counts match;
it must prevent saving a checkpoint that certifies a torn output pair.

Shared provider HTTP state belongs to `ChatDownloaderSession`, while cached
site instances and explicitly propagated cookies belong to `_SiteSessionPool`.
Neither module accepts its whole owning downloader or a protocol that
redeclares it. Provider-specific authentication checks and retry policy remain
with the provider and `BaseChatDownloader`.

**Revisit when:** a second production caller requires an existing internal
stage independently, or a new session implementation demonstrates a real seam
that cannot be expressed by the current internal adapter factory.

## Use reverse history for Kick replay completeness

**Decision:** VODs and clips use reverse cursors and a temporary spool before
chronological emission. Startup and short reconnect recovery also use reverse
cursors, with bounded in-memory collection. The cursor returned beside a
forward response is not a forward continuation token.

**Evidence:** A public recording's current website ID returned 404 from the
legacy video endpoint while the web gateway returned valid public metadata.
The legacy alias then returned an empty opening history page. Advancing its
forward cursor recovered only 201 records; overlapping its rounded cursor
recovered 408, with different IDs. Reverse pagination recovered 4,148 records,
including all records from both experiments. A bounded forward request returned
two of five available records. The website queries five-second windows.

This evidence replaces the earlier forward-streaming assumption. The cost is
higher time to first output and temporary storage proportional to the selected
replay. Message limits apply after history collection and filtering. Keep
source/clip identity checks, bounded-memory spooling, and explicit failure on
pagination loops. Do not restore cursor-derived forward traversal from
synthetic fixtures alone.

**2026-10-17 live evidence:** A ten-minute adinross capture omitted 193 retained
messages between its stale preload snapshot and first live delivery, including
39 sent after capture startup. Initial subscription now triggers history
recovery. A separate twenty-second audit retrieved 373 messages with reverse
cursors; five-second forward requests omitted 257 of those IDs because each
response held only 25 messages spanning about one or two seconds. Reverse
pagination now covers both recovery paths; budget exhaustion and retrieval
failures are recorded for inspection. A curated dense-page fixture preserves
page order, timestamps and cursors with replaced identities and text.

## Keep optional console compaction separate from capture output

**Decision:** `RunConfig.compact_console` defaults to false and applies only to
stdout. Providers select eligible state updates through `get_console_state`;
the shared runtime stores the latest complete payload fingerprint for each
selected identity in a per-run LRU cache capped at 128 entries. Kick selects
identified prediction snapshots. Creation notices and incomplete snapshots
always display and refresh the cache. Changed payloads remain visible, including
a return to a previously observed state. Uncomparable
payloads remain visible and reset the cache. File writers, accepted-message
counts, limits, and JSONL/TXT verification do not use this state cache.

**Evidence:** The 2026-10-17 ten-minute Kick capture included 8,939 prediction
updates, 22% of records and 34% of JSONL bytes. Of these, 1,332 repeated an
identical payload somewhere in the capture. This is evidence for an optional
console view, not permission to discard provider events from an archive.
Prediction text summaries expose observed question, state and vote fields;
resolved winner labels require an explicit, unique outcome identity.

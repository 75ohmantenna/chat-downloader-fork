# Twitch discovery deepening plan

**Status: implemented and verified.** This reviewed plan was committed after
the output-target implementation and before discovery changes. The sections
below preserve the intended design; the outcome records the completed work.

## Evidence and intended depth

`sites/twitch/discovery.py` repeats the same pagination state machine in
`get_user_clips`, `get_user_videos`, and `get_top_livestreams`. A recent recovery
change had to repair cursor progress validation in all three loops. Each caller
currently owns quota accounting, next-page decisions, cursor freshness, and the
ordering of those checks relative to yielding results.

Concentrate this behavior in one private edge iterator inside the existing
discovery module. Its interface accepts a page-fetch callable plus the limit,
page size, initial cursor, and discovery kind used in the existing diagnostic.
It owns the loop, remaining quota, seen cursors, page-size calculation, and
validate-before-yield ordering. The deletion test is straightforward: removing
this iterator would restore the same error-prone recipe to three callers.

Each endpoint retains query construction and connection lookup through a local
callable. Video user-existence validation and missing-stream warning behavior
stay with the endpoint that defines them. Existing edge/node normalization and
remapping stay at their current call sites. Do not change provider schemas,
public defaults, or the GraphQL operation-hash guard.

This gives locality to pagination repairs and leverage to three discovery
callers without introducing a new module, broad parent protocol, configurable
GraphQL framework, or forwarding methods. The seam is private to the module.
The existing injected GraphQL transport remains the true external dependency:
production and test adapters already exist. No new adapter interface is needed.

## Adversarial constraints

- Quota counts returned edges, including malformed edges and empty nodes, not
  the number of remapped results emitted by an endpoint.
- Preserve server overdelivery: do not newly trim a returned page to the
  requested quota. This is a refactor, not a limit-policy correction.
- Validate a required continuation before yielding any edge from its page.
  A missing, repeated, or cyclic cursor must fail at the same observation point.
- A terminal page or a page satisfying the quota requires no fresh cursor.
  Empty pages with a next-page claim must fail rather than repeat a request.
- Preserve page sizes: clips request at most 100, videos and streams at most 30.
  Preserve short-page accounting and unlimited video discovery.
- Preserve initial cursor differences: clips use base64-encoded zero, streams
  use the empty string, and videos omit the cursor. The initial non-null cursor
  participates in cycle detection; video discovery does not seed a null cursor.
- Zero or negative limits do not invoke the page fetcher. Preserve endpoint
  behavior for empty payloads, missing connections, and user-not-found results.
- Keep parsing and field mapping unchanged. If implementation reveals a need
  to reshape payload parsing, stop and promote a curated fixture first under
  the Twitch repository instructions.

## Alternatives and review

Sharing only a cursor-check helper would leave quota and ordering obligations
in all three callers. A state object requiring callers to advance and record
pages separately would likewise expose the recipe. A generic paginator with
query schemas, response selectors, and error callbacks would add unnecessary
configuration. The private iterator hides the whole repeated mechanism while
letting endpoint callables retain their actual differences.

The output writer activation candidate is not part of this work. Its existing
collective activation contract also coordinates rollback snapshots and custom
writers; replacing private accesses with prepare/validate/commit methods would
not remove caller obligations. No broader runtime or provider restructuring is
justified by the discovery evidence.

## Verification and completion

The interface is the test surface: exercise the three existing discovery
functions with the current mock GraphQL adapter. Retain their composition tests
and consolidate overlapping pagination cases into a cross-endpoint matrix
where doing so preserves endpoint-specific evidence. Add missing observable
contracts for quota accounting, server overdelivery, and validation timing;
do not add direct tests of the private iterator.

Run focused Twitch tests required by its scoped guide, then canonical `make ci`.
Maintain 100% offline line coverage, module-size and typing ratchets. Update the
owning architecture guide and durable decision only after checking the final
implementation. Commit implementation separately with the required SSH
signature and actual `gpt-6-astra` trailer.

Reassess the resulting module and nearby recent changes. Stop when a proposed
change would merely relocate code, add configuration, or reopen a closed
decision without new evidence.

## Review record and adjacent reassessment

Independent adversarial review accepted the private iterator after checking
edge-based quota accounting, server overdelivery, validation timing, initial
cursor differences, and endpoint-specific errors. Implementation followed the
required separate plan and prior implementation commits.

Read-only reassessment found no additional justified deepening:

- Session pooling and HTTP session ownership have distinct resource lifetimes:
  cached provider identity and explicit cookie propagation versus HTTP state.
  Merging them would couple responsibilities rather than remove caller facts.
- HTTP deadline, wait, TLS, and SOCKS modules implement real urllib3 variations.
  Session configuration already hides their composition. Combining their
  implementations would merely move protocol-specific knowledge.
- Shared retry policy already owns waiting and exhaustion. Provider decisions
  about useful progress, authentication, and recovery remain protocol-specific.
  YouTube continuation fallback changes session and replay state together;
  extracting it would require the loop's state and reopen an existing decision.
- Writer activation coordinates rollback snapshots, collective validation, and
  descriptor-pinned initialization. New per-writer phase methods would leave
  the same ordering obligations with the caller. A batch module would need
  extra rollback and custom-writer controls without a demonstrated new consumer.

Revisit these choices only when an independent consumer or a concrete sequencing
defect demonstrates an interface that removes obligations. Their size alone
does not justify further restructuring.

## Outcome

One private iterator now owns discovery pagination in the existing module.
Endpoint query construction, response selection, and remapping remain local.
Duplicated cursor tests were replaced with a cross-endpoint matrix at the
existing discovery interfaces; quota and overdelivery regressions preserve
the reviewed behavior. No direct iterator test or new adapter was introduced.

Independent final source, test, and documentation review found no blocking
issues. All 727 scoped Twitch tests passed. Canonical `make ci` passed with
6,193 offline tests, two skips, 56 network deselections, and 100% coverage of
14,352 source lines. Lint, import contracts, formatting, spelling, typing,
package build, and smoke checks passed without ratchet exceptions.

The adjacent reassessment above remains the stopping decision: no further
examined change concentrates knowledge enough to justify its interface and
regression cost. The two implemented plans resolve the concrete opportunities
found in this review; no speculative cleanup round is scheduled.

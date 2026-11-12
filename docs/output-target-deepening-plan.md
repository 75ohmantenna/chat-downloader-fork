# Output target deepening plan

**Status: unimplemented proposal.** This document describes intended work,
not current behavior. Review and commit this plan before implementation.

## Evidence and scope

Recent changes repeatedly touched output ownership, filename safety, metadata,
and verification. Current output target policy spans
`runtime/chat_pipeline.py`, `sites/output_dispatch.py`, and
`runtime/capture_manifest.py`. The pipeline parses template fields, decides
whether metadata is provisional, groups aliases, and propagates generated-file
exclusivity into a writer's private state. The dispatcher repeats filename and
identity policy before opening files. Manifest and verification callers expand
names again through `_capture_outputs`.

These observations justify concentrating output target policy. They do not
justify merging the early and final observations: early deduplication makes
verification see the effective pair, while final resolution uses metadata that
may arrive only when iteration starts. The existing decision to compare output
destinations after expansion remains binding.

## Intended interface and ownership

The pipeline will make one `configure_outputs` call in the existing output
dispatch module, passing the chat and explicit output options. This operation
will attach lazy writers and perform provisional selection. Remove the old
pipeline helper and its unused writer-factory injection. Keep custom writer
attachment through `Chat.attach_writer` and its lazy dispatcher construction.
Do not introduce another forwarding method on `Chat`.

Within output dispatch, a cohesive output-target module will own template
expansion, provisional identity, alias selection, generated provenance, and
read-only path observation. It will own the selected writer collection and the
state distinguishing original templates from finalized names. Its internal
seam will accept metadata values, not an entire chat or a broad parent protocol.
The structural writer interface will live with its actual consumers, without
an import-only compatibility barrel.

The dispatcher will retain collective file activation, emission, accounting,
record rollback, and shutdown. It will use target selection at the existing
early and final points. The new module separates two existing responsibilities;
it is not a split by execution phase or an excuse to bypass the line ratchet.

The dispatcher will expose read-only output path observation. Manifest and
verification code will consume it rather than import expansion helpers or
interpret writer initialization as template state. Delete `_capture_outputs`
and its `keep_created` switch. Preserve the writer-summary schema.

The deletion test is concrete: deleting the new target module would put
template state and identity rules back in multiple callers. Deleting the old
pipeline helper and manifest helper removes caller obligations. The gain in
depth is locality of policy and leverage for configuration, dispatch, and
artifact inspection; it is not a smaller file count.

## Adversarial constraints

- Provisional selection must not replace original names or mark them resolved.
  Unknown or pending title/ID metadata permits deduplication only of identical
  templates, not distinct templates that temporarily expand alike.
- Early selection may inspect path identity, as it does today, but must not
  check generated collisions, create files, repair tails, or truncate files.
- Final selection must use one identity snapshot for both alias grouping and
  generated-file policy. Generated provenance must survive when a fixed alias
  wins, regardless of attachment order.
- Resolve templates exactly once. Escaped braces and braces supplied by title
  or ID are literal after expansion. Observing paths must not mutate state.
- Eager writers retain their actual names. Custom writers retain attachment,
  initialization, emission, and close behavior. Configuration must not absorb
  provider knowledge or widen the writer interface to expose file internals.
- Configuration preserves existing attached writers and empty-output lazy
  behavior. Selection must not discard or close initialized alias writers.
  The selected collection and dispatcher accounting must remain aligned.
- Preserve preflight of the entire target set before destructive activation,
  descriptor-pinned rollback, successful-write counts, and close ordering.
- Do not weaken module-size, typing, architecture, coverage, or documentation
  ratchets. Do not add a filesystem port: temporary real files are the existing
  local-substitutable dependency. No remote adapter is needed.

Two suspicious existing observations require explicit regression evidence:
manifest binding expands an eager writer's literal filename, and a finalized
path whose writer fails to initialize can be expanded again. Reproduce these
through capture or manifest interfaces before treating corrected observation
as an intended behavior fix. Do not silently broaden the refactor.

## Alternatives considered

1. Move the existing pipeline helper unchanged. Rejected: duplicate policy and
   caller obligations remain, so the module gains no depth.
2. Share only an expansion function. Rejected: shared string substitution does
   not hide provisional state, exclusivity, or alias selection.
3. Add a generic output backend port. Rejected: there is no second justified
   adapter; custom writers already have their structural seam.
4. Deepen collective writer activation in the same change. Deferred: snapshots,
   custom writers, and commit ordering create a separate contract. Extra
   prepare/validate/commit methods alone would widen the interface.
5. Extract the runner's finalization lifecycle. Rejected: no new evidence
   overcomes the recorded decision to keep its eligibility facts together.

## Verification and completion

The interface is the test surface. Replace pipeline-helper tests with tests
through `configure_chat` or capture execution, rather than retaining old tests
and layering new ones. Preserve existing real-file regressions for provisional
metadata, known aliases, generated collisions, escaping, identity races,
verification, manifests, checkpoints, eager writers, and custom writers.
Keep targeted fault injection at internal filesystem seams where needed to
exercise races; assert observable preservation and cleanup outcomes.

Run focused output and runtime tests during implementation. Update the owning
architecture guide and durable output-target decision only after verifying
the resulting implementation. Run `make ci` for final validation, including
100% deterministic offline line coverage. Inspect the final diff for removed
caller obligations and unchanged public output behavior, aside from separately
reproduced filename-observation fixes.

Commit implementation separately from this plan using the required SSH signing
and the actual `gpt-6-astra` co-author trailer. Then reassess the remaining
writer-activation candidate. Stop when further work would add an interface or
move code without concentrating knowledge; do not manufacture another round.

## Review record

An independent adversarial review accepted this scope after requiring explicit
preservation of provisional metadata, generated alias policy, eager writers,
existing attachments, and read-only observation. A local reproduction confirmed
that manifest binding rejects an already opened literal `literal-{field}.jsonl`
filename as an unsupported template. Cover this concrete defect at the manifest
interface as part of concentrating filename ownership.

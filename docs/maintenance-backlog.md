# Maintenance Backlog

This file contains only current, evidence-backed maintainability work.
Completed work and standing design choices do not belong here; their durable
rationale is in
[`maintenance-decisions.md`](maintenance-decisions.md), while implementation
history remains available through Git and `CHANGELOG.md`.

## Adding an item

An item needs:

- an observed failure, recurring maintenance cost, or organically breached
  guardrail;
- a concrete owner or source area;
- a completion signal that can be tested or inspected;
- links to any decision that constrains the solution.

Do not add scheduled cleanup rounds or stale source metrics. Tests and source
code are authoritative for line counts, complexity, coverage, and `Any` floors.

## Active watchlist

### Kick official Public API

**Status: watch; no implementation work is currently justified.** Use the
official API as a schema reference and possible future authenticated option.
Do not replace the current read-only capture path until Kick exposes a
documented read-chat, replay-chat, or equivalent event stream that covers it.

- Official documentation: <https://docs.kick.com/>
- Documentation source: <https://github.com/KickEngineering/KickDevDocs>
- Relevant current surfaces: channel metadata, paginated livestream metadata,
  per-user live status, and chat, subscription, moderation, and gift webhook
  schemas.
- Current Chat API operations send and delete messages; they do not retrieve
  chat history or provide an unauthenticated live read stream.
- Revisit when the official API adds a read surface that can preserve current
  live, offline-channel, preloaded-history, and VOD behavior.

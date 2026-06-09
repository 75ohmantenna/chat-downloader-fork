# Maintenance Notes

Design decisions, deferred refactors, and non-obvious architectural choices
that are not obvious from the code or git history.

## Deferred cross-site deduplication

Three clusters of duplicated logic were flagged during the maintainability
pass (2026-06) and intentionally left unmerged. The decision for each is
documented here so a future contributor does not re-litigate it.

### 1. Remapping tables

**Files involved**

- `src/chat_downloader/sites/twitch/remappings.py` (~324 LOC)
- `src/chat_downloader/sites/youtube/constants_message.py` (~324 LOC)
- Shared machinery: `src/chat_downloader/sites/remap.py::Remapper`

**Why they look similar**

Both sites build large `dict[str, Remapper | str]` tables that are fed to
`Remapper.remap_dict`.  The table-building functions follow the same pattern:
literal field-name strings mapped to `Remapper` instances wrapping site-specific
parsing helpers.

**Why they were not merged**

The field semantics diverge per platform: YouTube uses `camelCase` InnerTube
field names; Twitch uses IRC tag keys and GraphQL snake_case fields.  The
transformations (colour handling, currency parsing, badge structures) are
platform-specific.  A shared declarative format would need to express both
domains without becoming a new DSL, and the two sites rarely add fields in
lockstep.

**Possible future abstraction**

A small declarative table format (e.g. a typed `FieldSpec` dataclass) that
both sites compile into `Remapper` dicts at import time.  Worth revisiting if a
third site is added or if the existing tables grow significantly.

---

### 2. Badge parsing

**Files involved**

- `src/chat_downloader/sites/twitch/parsing/badges.py`
- `src/chat_downloader/sites/youtube/parsing/message_content_badges.py`

**Why they look similar**

Both iterate a list of badge entries, pop known keys, build icon lists from
image URLs, and return a normalized `list[dict]`.

**Why they were not merged**

The source payloads are completely different (Twitch IRC tag strings vs YouTube
`liveChatAuthorBadgeRenderer` objects).  The surface similarity is in the output
shape, not the parsing logic.  A shared layer would require a protocol that
bridges incompatible input structures, adding indirection with no reduction in
parsing code.

---

### 3. Continuation error-recovery

**Files involved**

- `src/chat_downloader/sites/youtube/chat_streams_runtime_iteration.py`
  (`_get_chat_messages` — profile-fallback loop)
- `src/chat_downloader/sites/twitch/live_service.py` / `irc_transport.py`
  (IRC reconnect loop)

**Why they look similar**

Both loops catch transport-level errors, decide whether to retry or surface the
exception, and apply some form of back-off or state reset before continuing.

**Why they were not merged**

The transport mechanisms are fundamentally different: YouTube uses HTTP long-poll
continuations with a profile fallback mechanism; Twitch uses a persistent IRC
socket that must re-join the channel on reconnect.  The error types, retry
strategies, and state resets are transport-specific enough that a shared retry
policy object would carry more interface than logic.

A shared `RetryPolicy` (max_attempts, back-off function, on_retry callback) is
plausible as a pure-data object if the number of retry-carrying call sites grows.

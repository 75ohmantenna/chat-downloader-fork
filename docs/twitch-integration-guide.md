# Twitch Integration Guide

This guide explains how the Twitch integration works in
`chat-downloader-fork`. It is intended for maintainers debugging the live IRC
path or the GraphQL-backed replay path.

The Twitch stack is split across two transport families:

- IRC for live chat.
- GraphQL-backed APIs for metadata, replay comments, clips, and badges.

## What It Covers

The Twitch implementation is responsible for:

- matching Twitch live, VOD, and clip URLs
- retrieving stream or video metadata
- connecting to live IRC chat
- reading replay comments for VODs and clips
- parsing Twitch-specific message, badge, emote, reply, and system-event data

Primary entry point:

- `src/chat_downloader/sites/twitch/extractor.py`

Public site methods include `get_chat_by_stream_id`, `get_chat_by_vod_id`,
`get_chat_by_clip_id`, and `generate_urls`. URL matching routes live stream,
VOD, and clip URLs to those methods through `BaseChatDownloader.matches()`.

Main implementation areas:

- `src/chat_downloader/sites/twitch/`
- `src/chat_downloader/sites/twitch/parsing/`

## End-to-End Flow

The Twitch flow depends on the target type.

### Live streams

1. Resolve the channel from the URL.
2. Query Twitch GraphQL for stream metadata and status.
3. Refresh channel and global badge definitions.
4. Open an IRC connection to Twitch chat.
5. Join the channel and stream parsed IRC messages.
6. Filter, deduplicate, and normalize messages before yielding them.

### VODs and clips

1. Resolve the VOD or clip ID from the URL.
2. Query Twitch GraphQL for metadata.
3. Refresh badge definitions for the broadcaster.
4. Request replay comments in pages.
5. Parse comment nodes into normalized messages.
6. Apply time-range and message-type filtering until replay data is exhausted.

## Module Guide

### Site entry and orchestration

- `extractor.py`: `TwitchChatDownloader` and public site methods
- `live_service.py`: live chat orchestration
- `replay_service.py`: VOD and clip orchestration

### Transport and API access

- `irc_transport.py`: low-level Twitch IRC socket lifecycle
- `graphql_client.py`: persisted-query and bounded full-document GraphQL
  requests plus error mapping
- `badge_client.py`: channel/global badge retrieval, operation fallback, and
  response normalization
- `replay_transport.py`: replay comment retrieval
- `discovery.py` and `url_generation.py`: discovery helpers and generated URLs

### Parsing and shared Twitch data

- `parsing/messages.py`: high-level IRC and replay parsing orchestration
  (entry points `_parse_item` / `_parse_irc_item`)
- `parsing/message_emotes.py`: emote regex, image-list generation, author
  images, and text-with-emotes assembly
- `parsing/message_irc_resolve.py`: IRC action type, message type, room-state,
  shared-chat, ban/clearchat, and follower/slow-mode resolution
- `parsing/tag_decoding.py`: IRC tag decoding and boolean parsing
- `parsing/badges.py`: badge parsing and icon shaping helpers
- `remappings.py`: remapping dictionary builders
- `validation_keys.py`: known-key sets and raw replay-shape debug validation
- `types.py`: badge cache and related immutable snapshots
- `constants.py`: persisted-query names and hashes, message groups, IRC
  constants, GraphQL operation payloads, and known-key builders

There is no current `client.py` facade in the Twitch package. Import focused
modules directly when tests or integrations need patch points:
`graphql_client.py` for GraphQL, `irc_transport.py` for live IRC,
`replay_transport.py` for replay comments, and `parsing/` for message shaping.

## Live Capture Details

### Metadata first

The live path starts with a GraphQL metadata lookup. That determines whether the
channel is live, rerunning older content, offline, or upcoming.

The integration keeps the live-chat entry path available even when the channel
is not live yet, which allows callers to wait for chat activity.

### Badge refresh

Before parsing live traffic, the downloader fetches channel and global badges.
The result is stored in an instance-owned `BadgeCache`.

The legacy login-based operations remain primary. If Twitch rejects either
persisted hash, that badge source retries independently through the Android
client's current operation. Channel fallback uses the numeric channel ID from
stream, VOD, or clip metadata; the downloader retains that mapping for IRC
reconnect refreshes. Mobile `imageUrlNormal`, `imageUrlDouble`, and
`imageUrlQuadruple` fields are normalized to the existing cache shape without
discarding legacy click metadata.

The parser consumes a snapshot of badge data rather than mutating module-level
state. That keeps ownership clearer and testing simpler.

### IRC transport

Live chat uses Twitch IRC over TLS. The transport:

- requests Twitch IRC tags and commands capabilities
- connects anonymously with a `justinfan`-style nick
- joins the channel
- reads and parses IRC messages continuously
- responds to Twitch `PING` frames and sends periodic pings

The low-level `TwitchChatIRC` class lives in `irc_transport.py`. The live
service constructs it and passes parsed IRC frames through
`parsing/messages.py`.

Debug logging reports both the requested socket receive timeout and the
effective timeout. Twitch clamps values below one second to one second to
avoid idle CPU churn.

### Live diagnostics

Each live `Chat` exposes fixed-schema connection counters through
`chat.diagnostics`. The same mapping appears under `provider_diagnostics` in a
successful debug run summary.

| Field | Meaning |
| --- | --- |
| `optional_metadata_degradation_count` | Non-fatal result items with a recognized optional metadata service error |
| `connection_attempt_count` | IRC setup attempts |
| `connection_success_count` | Completed socket, timeout, and channel-join setup |
| `connection_setup_failure_count` | Setup attempts that failed before use |
| `reconnect_count` | Runtime disconnects that entered reconnect recovery |
| `server_reconnect_requested_count` | Parsed Twitch reconnect commands |
| `received_irc_chunk_count` | Non-empty socket receive chunks |
| `received_irc_frame_count` | Complete CRLF-delimited IRC frames |
| `parsed_irc_message_count` | Frames successfully parsed as Twitch messages |
| `receive_timeout_count` | Socket receive polls that timed out |
| `idle_watchdog_expiration_count` | Idle watchdog expirations that forced reconnect |
| `keepalive_ping_sent_count` | Periodic client `PING` commands sent |
| `keepalive_ping_received_count` | Server `PING` commands received |
| `keepalive_pong_sent_count` | Client `PONG` replies sent |
| `keepalive_pong_received_count` | Server `PONG` replies received |
| `duplicate_message_suppressed_count` | Repeated message IDs excluded from emission |
| `filtered_message_count` | Parsed messages excluded by type/group filters |
| `live_emitted_count` | Live messages emitted by the Twitch source generator |

The mapping contains only counters: it does not retain the channel, endpoint,
raw IRC frames, or chat content. Its keys are fixed so unexpected counter names
cannot grow retained state during a long capture.

The metadata-degradation counter increments once per GraphQL result item that
contains one or more explicitly recognized optional service errors, but only
after every item in that response has completed error handling without a fatal,
authentication, or persisted-hash failure. Repeated errors for the same optional
field in one result item count once. Separate non-fatal response items across
metadata retries count separately, including an item whose usable-data shape
later causes the caller to retry. A rejected persisted-query response contributes
nothing; an accepted degraded fallback response contributes normally. The
callback carries no path, message, channel, or response content.

### Filtering and deduplication

Before yielding live messages, the service:

- deduplicates recent message IDs
- ignores expected control traffic
- validates unexpected keys during debugging
- filters messages through configured message groups and message types

Current Android-client IRC events retain typed provider metadata. Paid pinned
chat remains a `text_message` and exposes its integer amount and exponent as
separate fields, avoiding lossy floating-point currency conversion. Charity
donations use the `charity` group, gift-sub matches use `subscriptions`, one-tap
breakpoint/gift/streak notices use `bits`, and moderator anniversaries use
`mods`. One-tap streak contributor fields are explicitly bounded to the three
positions supported by the client protocol.

### Shared Chat attribution

The IRC parser preserves raw Shared Chat tags and emits derived attribution
fields that are useful for analysis:

- `is_shared_chat_message`
- `shared_chat_effective_source_channel_id`
- `shared_chat_is_cross_channel`

## Replay Capture Details

### Replay pages

Replay chat depends on GraphQL metadata and replay comment pages rather than
IRC. The replay service:

- loads video or clip metadata
- determines duration and replay offsets
- pages through comment edges
- parses each comment node into the shared message schema

The legacy `VideoCommentsByOffsetOrCursor` operation remains primary. If
Twitch reports that its persisted hash is unavailable, replay retries through
the Android client's `VideoCommentsQuery` operation. The transport normalizes
that operation's explicit `from`/`to` emote positions and cursor-only terminal
signal into the existing replay contract before parsing. If Twitch also rejects
the mobile persisted hash, the client retries that operation once with the
exact full query document shipped by the Android client.

### Clip handling

Clips do not have their own standalone chat stream. The integration maps a clip
back to its source VOD and uses the clip offset plus clip duration to slice the
replay comment stream.

If the source VOD has expired, the integration raises `NoChatReplay`.

## GraphQL Dependency

Twitch metadata and replay capture depend on private persisted GraphQL queries.
That is the main fragility point in the Twitch stack.

Stream metadata, VOD metadata, and the mobile replay-comment operation have a
bounded full-document fallback. It runs only after Twitch explicitly reports a
missing persisted query; authentication, challenge, availability, and other
GraphQL errors retain their normal handling without a fallback request.

If Twitch rotates a hash or changes required variables, failures usually appear
first in:

- `graphql_client.py`
- `constants.py`
- replay or metadata tests

The default request profile support also includes `twitch_web`. Request profile
headers are built before CLI/user header overrides, so `--user-agent` and
repeatable `--header` values can replace profile defaults.

Request-profile names are validated when `DownloaderConfig` is created;
unknown values fail with `ValueError` instead of silently falling back to
generic headers.

The integration maps several GraphQL failure classes into clearer
downloader exceptions such as:

- `VideoNotFound`
- `VideoUnavailable`
- `LoginRequired`
- `VideoUnplayable`
- `ParsingError`

A Twitch `service error` for the optional `user.primaryTeam` field retains the
usable response data, logs at debug level, and contributes to the content-free
live metadata-degradation counter. The same error on an unfamiliar GraphQL path
remains a warning and does not increment the counter, so new partial-response
failures stay visible without being misclassified, including when Twitch returns
multiple errors together.

The default public Client-ID is defined in `constants.py`; callers can override
it with `DownloaderConfig(twitch_client_id=...)` or `--twitch_client_id`.

Twitch HTTP and IRC transports share the configured proxy policy. When no
explicit proxy is supplied, standard environment proxy variables may apply.
Combining cookies with an effective remote proxy is rejected before a session
is created; `proxy=""` explicitly opts out of environment proxies.

### GraphQL hash rotation

When Twitch rotates a persisted-query hash:

1. Update `OPERATION_HASHES` in `src/chat_downloader/sites/twitch/constants.py`.
2. Run `tests/test_twitch_drift_harness_unit.py`. Its coverage and orphan
   checks keep the table aligned with the operation names used by the client.

These are structural offline checks; no network access is required.

## Capture and Fix Parser Drift

For a bounded clean-run inspection of ordinary IRC traffic, enable
`CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`,
`CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1`, and debug logging. The first
three successfully parsed raw IRC frames are sanitized and captured across the
entire live run, including reconnects. This explicit second opt-in is required
because valid frames contain ordinary public chat data.

For broader event coverage, enable
`CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES=1` with the shared capture
flag and debug logging. This separate mode captures at most one successfully
parsed raw frame for each recognized event. When a frame carries `msg-id`, only
raw keys present in Twitch's `MESSAGE_TYPE_REMAPPING` qualify for a normalized
message key. An unknown raw value such as `resubscription` or `text_message`
uses the action fallback even though it resembles a normalized output. When
`msg-id` is absent, a known raw action may use its parsed normalized message
type; an unknown action uses the action fallback. Thus different unknown
`USERNOTICE` values share one action key rather than exhausting the quota or
aliasing a genuine normalized event. This classification reads raw tags without
adding provenance fields to normal output records.

Known normalized types and known raw actions retain readable labels with stable
digests. Unknown raw actions are sanitized before hashing and use only an
opaque `unknown-<digest>` component, so provider text or credential fragments
cannot enter event keys, filenames, or capture-path logs. Non-secret unknown
actions retain case- and punctuation-sensitive identity through that digest;
credential variants that sanitize to the same value intentionally share an
opaque key. Labels and in-memory keys are length-bounded and path-safe. Raw
frames remain sanitized and retain their original `\r\n` terminator.

The per-run sampler attempts at most 12 distinct event keys across all
reconnects. A failed write is retried once for that key, for at most 24 backend
capture attempts, and a key is considered captured only after the backend
returns a path. The shared backend adds a 12-sample group limit scoped to the
current process and absolute output directory. Consequently, a later run in
the same process that reuses that directory may have fewer than 12 group slots
available; 12 is a ceiling, not a guaranteed fresh allowance. The backend's
per-label `sample_limit=1` persists at that same scope: an exact payload from a
later run can resolve to its existing deterministic path, while a different
payload for the same event key is rejected even when aggregate group slots
remain.

The first-three and event-diverse modes are additive. With both enabled, clean
traffic capture writes at most 15 raw-frame samples: three first-arrival samples
plus 12 event-key samples. The same frame can appear once under each mode, so
review all captured public chat data before sharing it. Drift samples for
unknown types, tags, actions, and shapes use their own limits and are not part
of this clean-traffic maximum. Both raw modes run after parsing but before live
message deduplication and type or group filtering, so they can retain duplicates
or records excluded from normal JSONL/TXT output. An unknown frame can also
appear in both its drift sample and the event mode's raw-action fallback.

When a live IRC message or GraphQL response contains an unknown type, tag, or
shape, the runtime emits a drift diagnostic and saves a sanitized snapshot when
debug-sample capture is enabled. Unknown IRC actions, message types, tags, and
unmatched IRC shapes retain the original IRC line, including its `\r\n` line
terminator, so the snapshot can be promoted directly into a drift fixture.
Unexpected replay shapes retain the complete original GraphQL edge, including
fields that normalized remapping would otherwise discard. Each Twitch drift
label captures at most ten unique payloads per process and output directory, so
a newly ubiquitous provider field cannot create one file per message.
See [Debug sample capture](development-workflow-guide.md#debug-sample-capture)
for capture configuration. Promote reviewed samples into
`tests/fixtures/twitch/`.

For a bounded full-spectrum inspection of a live channel's message-retrieval
window, use the `all` message group and write both supported output formats:

```bash
capture_dir="$(mktemp -d)"
CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR="${capture_dir}/samples" \
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1 \
CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES=1 \
uv run chat_downloader "https://www.twitch.tv/auronplay" \
  --message_groups all \
  --logging debug \
  --timeout 240 \
  --output "${capture_dir}/chat.jsonl" \
  --output "${capture_dir}/chat.txt" \
  --quiet \
  2> "${capture_dir}/debug.log"
```

`--quiet` suppresses the duplicate formatted stream on standard output; it does
not disable debug logging or either output writer. After the run, inspect
`debug.log` for warnings, errors, reconnects, and drift diagnostics; inspect the
sanitized samples under `samples/`; and compare `chat.jsonl` with `chat.txt`.
`--timeout 240` bounds chat-message retrieval after setup; metadata and badge
setup plus final output shutdown can make the command's total wall time longer
than four minutes.

JSONL is the lossless record for checking message types and provider metadata,
while TXT verifies user-visible formatting. The raw-frame options capture only
the first three successfully parsed IRC frames and the first frame for up to 12
distinct event keys, not the entire conversation. Review captured public chat
data before sharing it.

Use `--message_groups all` for this output inspection because Twitch's default
`messages` group emits ordinary chat messages but omits known system and
moderation events such as subscriptions, room-state changes, deleted messages,
and bans. Unknown-action, unknown-type, unknown-tag, unmatched-shape, and
unexpected-key diagnostics run during parsing or on the parsed record before
message-group filtering. They therefore remain active with the default filter;
`all` removes message-group filtering from normal parsed output records observed
during the window rather than enabling drift detection itself. Transport-control
records and duplicate message IDs can still be handled internally. The option
also cannot make rare events occur; use curated fixtures or repeated targeted
runs when the complete parser surface needs coverage.

To turn a captured drift sample into a permanent regression anchor:

1. Reproduce the failure with
   `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1` and `--logging debug`.
2. Read and review the sanitized snapshot. The harness rejects any
   unexpected-data `debug_log()` call, unmatched IRC fixture, unexpected
   normalized IRC key, or raw GraphQL schema drift.
3. Update the relevant parser contract:
   - New IRC action type → extend `ACTION_TYPE_REMAPPING` in `constants.py`.
   - New IRC message type → extend `MESSAGE_GROUP_REMAPPINGS` in
     `constants.py` and assign it to the appropriate message group.
   - New IRC tag → add it to `build_irc_remapping()` or
     `build_message_param_remapping()` in `remappings.py`; the known-key set
     derives from those mappings.
   - New replay field or typename → update the VOD edge schema in
     `validation_keys.py`, then update the parser/remapping when the field is
     useful.
   - GraphQL hash rotation → update `OPERATION_HASHES` in `constants.py`; see
     [GraphQL hash rotation](#graphql-hash-rotation).
4. Promote reviewed IRC samples into `tests/fixtures/twitch/live_events/` and
   replay samples into `tests/fixtures/twitch/graphql/`. Protocol-derived
   synthetic IRC fixtures are also accepted when they are intentionally built
   from reviewed client evidence. The drift harness runs both fixture families
   through their real parser composition.
5. Run the drift harness, then the canonical validation:
   ```bash
   uv run pytest -q tests/test_twitch_drift_harness_unit.py
   make ci
   ```
   It replays every fixture and asserts no drift report fires. A passing harness
   makes the fix a permanent regression anchor.

## Common Failure Points

The Twitch stack is most sensitive to changes in:

- persisted GraphQL hashes
- replay comment schema
- IRC tags and message variants
- badge payload shape

When debugging Twitch breakage, inspect modules in this order:

1. `graphql_client.py` — GraphQL request structure and error mapping
2. `constants.py` — persisted-query hashes (`OPERATION_HASHES`) and message
   group/remapping tables
3. `validation_keys.py` — raw IRC and replay-shape known-key sets
4. `replay_service.py` or `live_service.py` — service-layer orchestration
5. `irc_transport.py` — low-level IRC socket and capability negotiation
6. `parsing/message_irc_resolve.py` — IRC action/message-type resolution
7. `parsing/message_emotes.py` — emote parsing and image-list assembly
8. `parsing/messages.py` — high-level orchestration and field assembly

## Testing

Parser, GraphQL, replay, and IRC transport tests are offline by default.
Reviewed drift fixtures live under `tests/fixtures/twitch/`; live-network tests
must carry `@pytest.mark.network` and require `--run-network`.
